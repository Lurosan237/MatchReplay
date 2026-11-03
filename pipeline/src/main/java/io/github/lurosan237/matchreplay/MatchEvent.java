package io.github.lurosan237.matchreplay;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/**
 * One match event as the pipeline receives it (see matchreplay/stream.py for the format).
 * A Flink POJO: public no-arg constructor and public fields.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class MatchEvent {
    public long match_id;
    public String home;
    public String away;
    public boolean women;
    /** Milliseconds on the match-long clock; always increases through a match. */
    public long event_time;
    public String event_id;
    public int index;
    public int period;
    public int minute;
    public int second;
    public String type;
    public String team;
    public String player;
    public Double x;
    public Double y;
    public Double end_x;
    public Double end_y;
    public String outcome;
    public Double xg;
    public String card;
    public Double duration;

    public MatchEvent() {}
}
