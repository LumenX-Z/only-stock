from collections.abc import Callable, Sequence


def test_run_concurrently_runs_every_callable(
    run_concurrently: Callable[[Sequence[Callable[[], object]]], list[object]],
) -> None:
    results = run_concurrently([lambda: 1, lambda: 2, lambda: 3])

    assert sorted(results) == [1, 2, 3]
