import numpy as np

from matchreplay.reconstruct import STEP_MS, FormationModel, limit_speed, smooth, to_metres


def test_statsbomb_yards_become_centred_metres_with_left_positive():
    x, y = to_metres([60, 120, 0], [40, 0, 80])
    assert np.allclose(x, [0, 52.5, -52.5])
    assert np.allclose(y, [0, 34, -34])         # StatsBomb y = 0 is the attacking team's left


def test_nobody_runs_faster_than_the_cap():
    pos = np.zeros((10, 1, 2))
    pos[5:, 0, 0] = 30                          # a 30 m jump in one step
    out = limit_speed(pos, vmax=8)
    steps = np.hypot(*np.diff(out[:, 0], axis=0).T)
    assert steps.max() <= 8 * STEP_MS / 1000 + 1e-9
    assert out[-1, 0, 0] > out[5, 0, 0]          # it still gets there, over several steps


def test_an_observation_pulls_nearby_times_more_than_far_ones():
    values, weights = np.zeros(200), np.zeros(200)
    values[100], weights[100] = 5.0, 1.0
    s, w = smooth(values, weights, sigma_steps=10)
    assert s[100] > s[115] > s[140] >= 0 and np.isclose(s[100] / w[100], 5.0)


def test_unseen_roles_fall_back_to_a_shape_that_moves_with_the_ball():
    m = FormationModel()
    roles = np.array(["Left Back", "Left Back"])
    ball = np.array([[-30.0, 0.0], [30.0, 0.0]])
    pred = m.predict(roles, ball, np.array([0.0, 1.0]))
    assert pred[1, 0] > pred[0, 0] and pred[0, 1] > 0     # pushes up with the ball, stays on the left
