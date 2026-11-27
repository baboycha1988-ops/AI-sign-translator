"""Dictionary search, sign proposals (module 20) and duplicate merging.
Pure domain logic over an in-memory repo; the PostgreSQL repo must implement the same methods
(statuses/transitions mirror the SQL triggers, which is checked by tests)."""
import uuid
from dataclasses import dataclass, field

from .core.lifecycle import GateResult, can_transition, evaluate_gate
from .core.permissions import has_permission
from .core.readiness import training_readiness


class DomainError(Exception):
    def __init__(self, code, message, http_status=400):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


@dataclass
class Actor:
    id: str
    roles: list


@dataclass
class DictRepo:
    signs: dict = field(default_factory=dict)
    proposals: dict = field(default_factory=dict)
    samples: list = field(default_factory=list)      # dicts: sign_id, proposal_id, signer_id, qc_passed, verified
    settings: dict = field(default_factory=lambda: {"dataset.min_signers_per_sign": 3,
                                                    "dataset.min_samples_per_sign": 15})


def _need(actor, perm):
    if not has_permission(actor.roles, perm):
        raise DomainError("forbidden", f"permission '{perm}' required", 403)


def _norm(s):
    return " ".join(s.lower().split())


class DictionaryService:
    def __init__(self, repo: DictRepo):
        self.repo = repo

    def add_sign(self, actor, *, gloss, sign_language, translations, description="", status="draft"):
        _need(actor, "signs.review")
        return self._create_sign(gloss, sign_language, translations, description, status)

    def _create_sign(self, gloss, sign_language, translations, description, status):
        gloss = gloss.strip()
        if not 1 <= len(gloss) <= 120:
            raise DomainError("invalid_gloss", "gloss must be 1..120 characters")
        if not translations:
            raise DomainError("no_translation", "at least one translation is required")
        if any(s["gloss"] == gloss and s["language"] == sign_language and s["status"] != "merged"
               for s in self.repo.signs.values()):
            raise DomainError("duplicate_gloss", "a sign with this name already exists", 409)
        sid = str(uuid.uuid4())
        self.repo.signs[sid] = {"id": sid, "gloss": gloss, "language": sign_language, "description": description,
                                "status": status, "merged_into": None,
                                "translations": [(l, t.strip()) for l, t in translations]}
        return self.repo.signs[sid]

    def find_duplicates(self, sign_language, gloss, translations):
        g = _norm(gloss)
        wanted = {(l, _norm(t)) for l, t in translations}
        out = []
        for s in self.repo.signs.values():
            if s["language"] != sign_language or s["status"] == "merged":
                continue
            if _norm(s["gloss"]) == g or wanted & {(l, _norm(t)) for l, t in s["translations"]}:
                out.append(s["id"])
        return out

    def search(self, query, lang):
        q = _norm(query)
        res = []
        for s in self.repo.signs.values():
            if s["status"] not in ("approved", "active"):
                continue
            if any(l == lang and q in _norm(t) for l, t in s["translations"]):
                res.append({"id": s["id"], "gloss": s["gloss"], "status": s["status"],
                            "recognizable": s["status"] == "active"})
        return res

    def merge(self, actor, source_id, target_id):
        _need(actor, "signs.review")
        src, tgt = self.repo.signs.get(source_id), self.repo.signs.get(target_id)
        if not src or not tgt:
            raise DomainError("not_found", "sign not found", 404)
        if source_id == target_id:
            raise DomainError("invalid_merge", "cannot merge a sign into itself")
        if src["status"] == "merged" or tgt["status"] == "merged":
            raise DomainError("invalid_merge", "merged signs cannot take part in a merge")
        if src["language"] != tgt["language"]:
            raise DomainError("invalid_merge", "signs of different sign languages are never merged")
        for tr in src["translations"]:
            if tr not in tgt["translations"]:
                tgt["translations"].append(tr)
        for smp in self.repo.samples:
            if smp.get("sign_id") == source_id:
                smp["sign_id"] = target_id
        src["status"], src["merged_into"] = "merged", target_id
        return tgt


class ProposalService:
    def __init__(self, repo: DictRepo):
        self.repo, self.dict = repo, DictionaryService(repo)

    def _get(self, pid):
        p = self.repo.proposals.get(pid)
        if not p:
            raise DomainError("not_found", "proposal not found", 404)
        return p

    def _move(self, p, new):
        if not can_transition(p["status"], new):
            raise DomainError("invalid_transition", f"{p['status']} -> {new} is not allowed", 409)
        p["status"] = new

    def submit(self, actor, *, gloss, sign_language, translations, description=""):
        _need(actor, "signs.propose")
        if not gloss.strip() or not translations:
            raise DomainError("invalid_proposal", "name and at least one translation are required")
        pid = str(uuid.uuid4())
        self.repo.proposals[pid] = {"id": pid, "proposer": actor.id, "gloss": gloss.strip(), "language": sign_language,
                                    "translations": list(translations), "description": description,
                                    "status": "submitted", "note": None, "duplicate_of": None, "sign_id": None}
        return {"proposal": self.repo.proposals[pid],
                "possible_duplicates": self.dict.find_duplicates(sign_language, gloss, translations)}

    def list_own(self, actor):
        return [p for p in self.repo.proposals.values() if p["proposer"] == actor.id]

    def review(self, actor, pid, decision, *, note=None, duplicate_of=None):
        _need(actor, "signs.review")
        p = self._get(pid)
        self._move(p, "in_review")
        if decision == "approve":
            sign = self.dict._create_sign(p["gloss"], p["language"], p["translations"], p["description"], "approved")
            p["sign_id"] = sign["id"]
            self._move(p, "approved")
        elif decision == "reject":
            if not note:
                p["status"] = "submitted"
                raise DomainError("note_required", "a reason is required to reject", 400)
            p["note"] = note
            self._move(p, "rejected")
        elif decision == "duplicate":
            if duplicate_of not in self.repo.signs:
                p["status"] = "submitted"
                raise DomainError("not_found", "duplicate target sign not found", 404)
            p["duplicate_of"] = duplicate_of
            self._move(p, "merged_duplicate")
        else:
            p["status"] = "submitted"
            raise DomainError("invalid_decision", "decision must be approve, reject or duplicate")
        return p

    def start_collecting(self, actor, pid):
        _need(actor, "dataset.manage")
        p = self._get(pid); self._move(p, "collecting_samples"); return p

    def mark_ready(self, actor, pid):
        _need(actor, "dataset.manage")
        p = self._get(pid)
        s = self.repo.settings
        rows = [(x["signer_id"], x["qc_passed"], x["verified"]) for x in self.repo.samples
                if x.get("proposal_id") == pid or (p["sign_id"] and x.get("sign_id") == p["sign_id"])]
        r = training_readiness(rows, min_signers=s["dataset.min_signers_per_sign"],
                               min_samples=s["dataset.min_samples_per_sign"])
        if not r.ready:
            raise DomainError("not_ready", "; ".join(r.reasons), 409)
        self._move(p, "ready_for_training"); return p

    def start_training(self, actor, pid):
        _need(actor, "models.manage")
        p = self._get(pid); self._move(p, "in_training"); return p

    def finish_training(self, actor, pid):
        _need(actor, "models.manage")
        p = self._get(pid); self._move(p, "evaluating"); return p

    def conclude(self, actor, pid, *, old_f1_before, old_f1_after, new_f1, min_new_sign_f1, max_regression):
        """Publish only if the verification gate passes; otherwise the proposal is marked failed."""
        _need(actor, "models.publish")
        p = self._get(pid)
        res: GateResult = evaluate_gate(old_f1_before=old_f1_before, old_f1_after=old_f1_after, new_f1=new_f1,
                                        min_new_sign_f1=min_new_sign_f1, max_regression=max_regression)
        if res.passed:
            self._move(p, "published")
            self.repo.signs[p["sign_id"]]["status"] = "active"
        else:
            self._move(p, "failed")
        return res
