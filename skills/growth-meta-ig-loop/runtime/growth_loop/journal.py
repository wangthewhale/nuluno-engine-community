from .errors import UncertainWrite, WorkflowError
from .files import digest


class Journal:
    """Persist intent before a write and reconcile unknown outcomes before retrying."""

    def __init__(self, store, task):
        self.store, self.task = store, task

    def execute(self, suffix, request, perform, reconcile=None):
        self.store.assert_lease(self.task)
        key = self.task["key"] + ":" + suffix
        op = self.store.prepare_operation(key, self.task["key"], digest(request), request)
        if op["state"] == "succeeded":
            return op["result"]
        if op["state"] in ("in_flight", "uncertain"):
            found = reconcile() if reconcile else None
            if found is not None:
                self.store.set_operation(key, "succeeded", found)
                self.store.event(self.task["run_id"], self.task["key"], "write_reconciled", {"operation": suffix})
                return found
            self.store.set_operation(key, "uncertain", {"reason": "No authoritative readback yet"})
            raise UncertainWrite("前次寫入結果仍不明；已保留進度，先對帳，不重複提交。", {"operation": suffix})
        self.store.set_operation(key, "in_flight")
        try:
            result = perform()
        except UncertainWrite:
            self.store.set_operation(key, "uncertain")
            found = reconcile() if reconcile else None
            if found is not None:
                self.store.set_operation(key, "succeeded", found)
                self.store.event(self.task["run_id"], self.task["key"], "write_reconciled", {"operation": suffix})
                return found
            raise
        except WorkflowError as exc:
            # An explicit Graph error response is a known rejection. A timeout is
            # handled above and is never treated as proof of non-commit.
            self.store.set_operation(key, "rejected", {"kind": exc.kind, "details": exc.details})
            raise
        except BaseException:
            # A crash can occur after the platform accepted a request.
            self.store.set_operation(key, "uncertain")
            raise
        self.store.set_operation(key, "succeeded", result)
        return result
