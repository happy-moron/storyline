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
    parser.addoption(
        "--run-realworld",
        action="store_true",
        default=False,
        help="run RealWorld E2E tests against live infrastructure",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as requiring live services (LLM, TTS, etc.)")
    config.addinivalue_line("markers", "slow: mark test as slow (benchmarks, full pipeline runs)")
    config.addinivalue_line("markers", "realworld: mark test as a RealWorld E2E test (live infra, no mocks)")


def pytest_collection_modifyitems(config, items):
    run_integration = config.getoption("--run-integration")
    run_slow = config.getoption("--run-slow")
    run_realworld = config.getoption("--run-realworld")

    skip_integration = pytest.mark.skip(reason="need --run-integration to run live service tests")
    skip_slow = pytest.mark.skip(reason="need --run-slow to run slow tests")
    skip_realworld = pytest.mark.skip(reason="need --run-realworld to run RealWorld tests")

    for item in items:
        if "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)
        if "realworld" in item.keywords and not run_realworld:
            item.add_marker(skip_realworld)