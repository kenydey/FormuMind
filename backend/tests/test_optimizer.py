from app.services.optimizer import BayesianOptimizer, Factor


def test_optimizer_improves_on_quadratic():
    # Maximise -(x-7)^2 - (y-30)^2 ; optimum at (7, 30).
    factors = [Factor("x", 0, 14), Factor("y", 0, 60)]
    opt = BayesianOptimizer(factors=factors, seed=1)

    def objective(p):
        return -((p[0] - 7.0) ** 2) - ((p[1] - 30.0) ** 2)

    for _ in range(60):
        x = opt.suggest()
        opt.observe(x, objective(x))

    best_x, best_y = opt.best
    # Should get reasonably close to the optimum.
    assert best_y > -25.0
    assert abs(best_x[0] - 7.0) < 4.0


def test_ranked_returns_sorted_top_n():
    factors = [Factor("x", 0, 10)]
    opt = BayesianOptimizer(factors=factors, seed=2)
    for v in [1.0, 5.0, 9.0, 3.0]:
        opt.observe([v], v)
    ranked = opt.ranked(2)
    assert [score for _, score in ranked] == [9.0, 5.0]


def test_respects_bounds():
    factors = [Factor("x", 2, 4)]
    opt = BayesianOptimizer(factors=factors, seed=3)
    for _ in range(20):
        x = opt.suggest()
        assert 2.0 <= x[0] <= 4.0
        opt.observe(x, -x[0])
