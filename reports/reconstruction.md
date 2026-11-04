# Reconstructed movement: how close to reality

For the matches without tracking, every player's path is reconstructed from the event data (see `src/matchreplay/reconstruct.py`). To find out how good that is, it is run on World Cup 2022 matches, where PFF's tracking says where everyone really was. The formation model is learned on 32 matches and scored on the other 32, which it never saw.

| Method | Mean error | Median error | Within 5 m |
|---|---|---|---|
| Formation only (no ball) | 23.0 m | 20.4 m | 6% |
| Formation moving with the ball | 11.7 m | 10.1 m | 19% |
| + the player on the ball | 10.9 m | 9.2 m | 23% |
| + the 360 frames (full reconstruction) | 10.0 m | 8.0 m | 30% |

Error is the distance between where the reconstruction puts a player and where the tracking says they were, over every player on the pitch, five times a second.

By distance from the ball (the 360 frames only see the area around it):

| Player's distance from the ball | Share of positions | Formation moving with the ball | Full reconstruction |
|---|---|---|---|
| 0-15 m | 25% | 11.4 m | 8.3 m |
| 15-30 m | 37% | 11.1 m | 9.3 m |
| over 30 m | 38% | 12.4 m | 12.0 m |

Near the ball, where the action is and the data sees most, the reconstruction is clearly closest; far from it, players are rarely seen and stay close to what the formation model predicts.

By match (mean error, full reconstruction):

| Match | Mean error |
|---|---|
| Japan v Spain | 8.1 m |
| Spain v Germany | 8.7 m |
| Brazil v Switzerland | 9.0 m |
| Spain v Costa Rica | 9.0 m |
| France v Australia | 9.1 m |
| Croatia v Canada | 9.2 m |
| Senegal v Netherlands | 9.2 m |
| Portugal v Switzerland | 9.3 m |
| England v Senegal | 9.3 m |
| Japan v Costa Rica | 9.6 m |
| Morocco v Spain | 9.7 m |
| Tunisia v France | 9.7 m |
| Morocco v Croatia | 9.8 m |
| Wales v England | 9.8 m |
| France v Morocco | 9.9 m |
| Argentina v Mexico | 9.9 m |
| Mexico v Poland | 10.0 m |
| Qatar v Senegal | 10.1 m |
| Cameroon v Serbia | 10.2 m |
| France v Poland | 10.2 m |
| England v France | 10.2 m |
| Ghana v Uruguay | 10.2 m |
| South Korea v Ghana | 10.3 m |
| England v Iran | 10.3 m |
| Uruguay v South Korea | 10.3 m |
| Australia v Denmark | 10.3 m |
| Wales v Iran | 10.4 m |
| Belgium v Morocco | 10.4 m |
| Ecuador v Senegal | 10.6 m |
| Poland v Saudi Arabia | 10.8 m |
| Morocco v Portugal | 11.5 m |
| Argentina v France | 14.5 m |
