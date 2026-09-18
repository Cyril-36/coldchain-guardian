"""Frozen holdout evaluation set. Labels live here and nowhere else.

This package sits outside backend/, which is the CodeUri for both Lambda bundles in
infra/template.yaml, so expected answers cannot reach a deployed runtime.
"""
