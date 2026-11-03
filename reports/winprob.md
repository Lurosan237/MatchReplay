# Live win probability

A goal-rate model for each team (Poisson regression), played forward minute by minute to the end of the phase. See `src/matchreplay/winprob.py` for the method.

## How good is it

Leave-one-competition-out: each of the 12 competitions is forecast by a model trained on the other 11, so every one of the 558 matches is forecast without having been seen. 35,268 forecasts, one per minute of play.

| Forecast | Log loss | Brier score |
|---|---|---|
| This model | 0.745 | 0.437 |
| Score and clock only | 0.760 | 0.446 |
| Always the average outcome | 1.077 | 0.652 |

Lower is better. "Score and clock only" knows the score, the time left and the game state; the full model also reads the match: expected goals for and against so far, and red cards.

By competition (Brier score, unseen):

| Competition | Matches | This model | Score and clock only |
|---|---|---|---|
| afcon2023 | 52 | 0.534 | 0.531 |
| bundesliga2324 | 34 | 0.386 | 0.398 |
| copa2024 | 32 | 0.420 | 0.440 |
| euro2020 | 51 | 0.460 | 0.470 |
| euro2024 | 51 | 0.501 | 0.522 |
| ligue12223 | 32 | 0.335 | 0.362 |
| wc2018 | 64 | 0.480 | 0.490 |
| wc2022 | 64 | 0.459 | 0.453 |
| weuro2022 | 31 | 0.425 | 0.446 |
| weuro2025 | 31 | 0.469 | 0.471 |
| wwc2019 | 52 | 0.373 | 0.391 |
| wwc2023 | 64 | 0.354 | 0.352 |

Reading the match helps in 9 of the 12 competitions; where it doesn't, the two are within a few thousandths. Tournaments full of upsets (AFCON 2023 is the clearest) are hard for any model that knows nothing about the teams before kick-off: that is the obvious next feature.

By stage of the match (Brier score):

| Minutes | This model | Score and clock only |
|---|---|---|
| 0-30 | 0.601 | 0.611 |
| 30-60 | 0.464 | 0.479 |
| 60-90 | 0.287 | 0.292 |
| 90-125 | 0.184 | 0.184 |

## Calibration

When the model gives the home team X%, does it win X% of the time?

| Predicted home win | Forecasts | Average prediction | Home team won |
|---|---|---|---|
| 0.0-0.1 | 6,989 | 0.03 | 0.04 |
| 0.1-0.2 | 3,307 | 0.15 | 0.14 |
| 0.2-0.3 | 3,136 | 0.25 | 0.22 |
| 0.3-0.4 | 5,585 | 0.36 | 0.36 |
| 0.4-0.5 | 5,554 | 0.43 | 0.45 |
| 0.5-0.6 | 649 | 0.54 | 0.56 |
| 0.6-0.7 | 861 | 0.66 | 0.61 |
| 0.7-0.8 | 2,112 | 0.76 | 0.77 |
| 0.8-0.9 | 2,219 | 0.85 | 0.84 |
| 0.9-1.0 | 4,856 | 0.98 | 0.96 |

## What it learned (all 558 matches)

Multipliers on a team's goal rate (1.00 = no effect):

| Feature | Multiplier |
|---|---|
| home | 1.17 |
| lead1 | 1.06 |
| lead2 | 1.25 |
| trail1 | 1.07 |
| trail2 | 1.10 |
| own_red | 0.51 |
| opp_red | 1.37 |
| xg_for | 1.38 |
| xg_against | 0.80 |
| women | 0.96 |
| extra_time | 0.69 |

`xg_for` and `xg_against` are per extra expected goal per 90 minutes, so far in the match (pulled towards the average early on).
