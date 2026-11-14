"""Smoke test: the llmgw package imports cleanly."""


def test_import_llmgw():
    import llmgw

    assert llmgw.__version__ == "0.1.0"
