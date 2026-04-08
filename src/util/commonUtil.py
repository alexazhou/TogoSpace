def first_not_none(*values):
    """返回第一个不为 None 的值，全为 None 时返回 None。"""
    return next((v for v in values if v is not None), None)
