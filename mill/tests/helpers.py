from mill.models import Lumber, LumberSource


def make_lumber(log=None, count=1, sources=None, **kwargs):
    """Create a Lumber batch plus its LumberSource rows.

    Single-log batch: make_lumber(log, count=N, **fields).
    Multi-log batch:  make_lumber(sources=[(log_a, 2), (log_b, 3)], **fields).
    """
    lumber = Lumber.objects.create(**kwargs)
    rows = sources if sources is not None else ([(log, count)] if log is not None else [])
    for lg, c in rows:
        LumberSource.objects.create(lumber=lumber, log=lg, count=c)
    return lumber
