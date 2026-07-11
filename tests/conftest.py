import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run integration tests that require live services",
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="run slow tests (benchmarks, full pipelines)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as requiring live services (LLM, TTS, etc.)")
    config.addinivalue_line("markers", "slow: mark test as slow (benchmarks, full pipeline runs)")


def pytest_collection_modifyitems(config, items):
    run_integration = config.getoption("--run-integration")
    run_slow = config.getoption("--run-slow")

    skip_integration = pytest.mark.skip(reason="need --run-integration to run live service tests")
    skip_slow = pytest.mark.skip(reason="need --run-slow to run slow tests")

    for item in items:
        if "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)