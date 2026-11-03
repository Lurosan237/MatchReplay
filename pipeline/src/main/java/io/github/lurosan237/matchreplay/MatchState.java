package io.github.lurosan237.matchreplay;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The live state of one match: a line-by-line port of MatchState in matchreplay/stream.py.
 * The parity test checks the two produce the same snapshots.
 */
public class MatchState {
    static final int SHOOTOUT = 5;
    static final double MOMENTUM_HALF_LIFE_S = 300.0;
    static final double BOX_X = 102.0, BOX_Y_MIN = 18.0, BOX_Y_MAX = 62.0;
    static final Set<String> ON_TARGET = Set.of("Goal", "Saved", "Saved To Post");

    /** One team's numbers. */
    public static class Side {
        public int goals, shots, on_target, passes, passes_completed, box_entries, fouls, yellow, red, shootout_goals;
        public double xg, momentum;

        public Side() {}

        Map<String, Object> toMap() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("goals", goals);
            m.put("shots", shots);
            m.put("on_target", on_target);
            m.put("xg", round4(xg));
            m.put("passes", passes);
            m.put("passes_completed", passes_completed);
            m.put("box_entries", box_entries);
            m.put("fouls", fouls);
            m.put("yellow", yellow);
            m.put("red", red);
            m.put("shootout_goals", shootout_goals);
            m.put("momentum", round4(momentum));
            return m;
        }
    }

    public long match_id;
    public String home, away;
    public Side homeSide = new Side(), awaySide = new Side();
    public int minute = 0, period = 1;
    public long last_time = 0;
    public int events = 0;

    public MatchState() {}

    public MatchState(long matchId, String home, String away) {
        this.match_id = matchId;
        this.home = home;
        this.away = away;
    }

    private Side side(String team) {
        if (home.equals(team)) return homeSide;
        if (away.equals(team)) return awaySide;
        return null;
    }

    private void decay(long t) {
        if (t > last_time) {
            double factor = Math.pow(0.5, (t - last_time) / 1000.0 / MOMENTUM_HALF_LIFE_S);
            homeSide.momentum *= factor;
            awaySide.momentum *= factor;
            last_time = t;
        }
    }

    private static boolean inBox(Double x, Double y) {
        return x != null && y != null && x >= BOX_X && y >= BOX_Y_MIN && y <= BOX_Y_MAX;
    }

    /** Apply one event; events must come in event-time order (ties by index). */
    public void apply(MatchEvent m) {
        decay(m.event_time);
        events++;
        minute = m.minute;
        period = m.period;
        Side s = m.team == null ? null : side(m.team);
        if (s == null) return;
        String kind = m.type;
        double xg = m.xg == null ? 0.0 : m.xg;
        if ("Shot".equals(kind)) {
            if (m.period == SHOOTOUT) {
                if ("Goal".equals(m.outcome)) s.shootout_goals++;
                return;
            }
            s.shots++;
            if (m.outcome != null && ON_TARGET.contains(m.outcome)) s.on_target++;
            if ("Goal".equals(m.outcome)) s.goals++;
            s.xg += xg;
            s.momentum += xg;
        } else if ("Own Goal For".equals(kind) && m.period != SHOOTOUT) {
            s.goals++;
        } else if ("Pass".equals(kind)) {
            s.passes++;
            if (m.outcome == null) {
                s.passes_completed++;
                if (inBox(m.end_x, m.end_y)) {
                    s.box_entries++;
                    s.momentum += 0.05;
                }
            }
        } else if ("Carry".equals(kind) && inBox(m.end_x, m.end_y) && !inBox(m.x, m.y)) {
            s.box_entries++;
            s.momentum += 0.05;
        } else if ("Foul Committed".equals(kind)) {
            s.fouls++;
        }
        if ("Yellow Card".equals(m.card)) s.yellow++;
        else if ("Second Yellow".equals(m.card) || "Red Card".equals(m.card)) s.red++;
    }

    /** The state as of event time t (ms), in the same shape as the Python snapshot. */
    public Map<String, Object> snapshot(long t) {
        decay(t);
        int totalPasses = homeSide.passes + awaySide.passes;
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("match_id", match_id);
        m.put("time", t);
        m.put("minute", minute);
        m.put("period", period);
        m.put("events", events);
        m.put("home", home);
        m.put("away", away);
        m.put("score", List.of(homeSide.goals, awaySide.goals));
        m.put("shootout", List.of(homeSide.shootout_goals, awaySide.shootout_goals));
        m.put("home_stats", homeSide.toMap());
        m.put("away_stats", awaySide.toMap());
        m.put("possession_home", totalPasses == 0 ? 0.5 : round4((double) homeSide.passes / totalPasses));
        m.put("momentum", round4(homeSide.momentum - awaySide.momentum));
        return m;
    }

    static double round4(double v) {
        return Math.round(v * 10_000.0) / 10_000.0;
    }
}
