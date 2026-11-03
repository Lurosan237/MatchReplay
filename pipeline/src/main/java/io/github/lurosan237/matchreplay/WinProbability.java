package io.github.lurosan237.matchreplay;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.List;

/**
 * Live win probability, computed inside the stream: a line-by-line port of matchreplay/winprob.py
 * (see there for the model). The coefficients come from models/winprob.json, trained in Python and
 * packaged into the job jar; the parity test checks Java and Python agree on every snapshot.
 */
public final class WinProbability {
    static final double[] PERIOD_LENGTH = {0, 0, 0, 0, 0};
    static final double[] PERIOD_START_MIN = {0, 0.0, 45.0, 90.0, 105.0};
    static final long[] PERIOD_OFFSET_MIN = {0, 0, 60, 120, 140, 160};
    static final double[] BETA;
    static final int MAX_DIFF;
    static final double MIN_REMAINING, XG_PRIOR_PER_90, XG_PRIOR_MINUTES;

    static {
        try (InputStream in = WinProbability.class.getResourceAsStream("/winprob.json")) {
            JsonNode model = new ObjectMapper().readTree(in);
            JsonNode beta = model.get("beta");
            BETA = new double[beta.size()];
            for (int i = 0; i < BETA.length; i++) BETA[i] = beta.get(i).asDouble();
            for (int p = 1; p <= 4; p++) PERIOD_LENGTH[p] = model.get("period_length").get(String.valueOf(p)).asDouble();
            MAX_DIFF = model.get("max_diff").asInt();
            MIN_REMAINING = model.get("min_remaining").asDouble();
            XG_PRIOR_PER_90 = model.get("xg_prior_per_90").asDouble();
            XG_PRIOR_MINUTES = model.get("xg_prior_minutes").asDouble();
        } catch (java.io.IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private WinProbability() {}

    static double elapsedInPeriod(long t, int period) {
        return (t - PERIOD_OFFSET_MIN[period] * 60_000L) / 60_000.0;
    }

    static double remainingMinutes(long t, int period) {
        double left = Math.max(PERIOD_LENGTH[period] - elapsedInPeriod(t, period), MIN_REMAINING);
        if (period == 1 || period == 3) left += PERIOD_LENGTH[period + 1];
        return left;
    }

    static double minutesPlayed(long t, int period) {
        return PERIOD_START_MIN[period] + Math.max(elapsedInPeriod(t, period), 0.0);
    }

    static double shrunkPer90(double xg, double played) {
        return (xg + XG_PRIOR_PER_90 / 90 * XG_PRIOR_MINUTES) / (played + XG_PRIOR_MINUTES) * 90;
    }

    static double[] features(boolean home, int diff, int ownRed, int oppRed, double xgFor, double xgAgainst,
                             boolean women, boolean extraTime) {
        return new double[] {1.0, home ? 1 : 0, diff == 1 ? 1 : 0, diff >= 2 ? 1 : 0, diff == -1 ? 1 : 0,
                diff <= -2 ? 1 : 0, Math.min(ownRed, 2), Math.min(oppRed, 2), xgFor - XG_PRIOR_PER_90,
                xgAgainst - XG_PRIOR_PER_90, women ? 1 : 0, extraTime ? 1 : 0};
    }

    static double rate(double[] x) {
        double s = 0;
        for (int i = 0; i < BETA.length; i++) s += BETA[i] * x[i];
        return Math.exp(s);
    }

    /** P(home wins, draw, away wins) at the end of the phase. */
    static List<Double> outcome(int diff, double remaining, int homeRed, double homeXg, int awayRed, double awayXg,
                                boolean women, boolean extraTime) {
        int n = 2 * MAX_DIFF + 1;
        double[] rh = new double[n], ra = new double[n];
        for (int i = 0; i < n; i++) {
            rh[i] = rate(features(true, diff - MAX_DIFF + i, homeRed, awayRed, homeXg, awayXg, women, extraTime));
            ra[i] = rate(features(false, MAX_DIFF - diff - i, awayRed, homeRed, awayXg, homeXg, women, extraTime));
        }
        double[] p = new double[n];
        p[MAX_DIFF] = 1.0;
        int steps = Math.max(1, (int) Math.ceil(remaining));
        for (int step = 0; step < steps; step++) {
            double dt = step == steps - 1 ? remaining - (steps - 1) : 1.0;
            double[] q = new double[n];
            for (int i = 0; i < n; i++) {
                if (p[i] == 0.0) continue;
                double gh = 1.0 - Math.exp(-rh[i] * dt);
                double ga = 1.0 - Math.exp(-ra[i] * dt);
                q[i] += p[i] * ((1.0 - gh) * (1.0 - ga) + gh * ga);
                q[Math.min(i + 1, n - 1)] += p[i] * gh * (1.0 - ga);
                q[Math.max(i - 1, 0)] += p[i] * ga * (1.0 - gh);
            }
            p = q;
        }
        double win = 0, draw = 0;
        for (int i = 0; i < n; i++) {
            int d = diff - MAX_DIFF + i;
            if (d > 0) win += p[i];
            else if (d == 0) draw += p[i];
        }
        return List.of(MatchState.round4(win), MatchState.round4(draw), MatchState.round4(1.0 - win - draw));
    }

    /**
     * Win probability for a snapshot's numbers (score, red cards, xG rounded as in the snapshot).
     * Null during the penalty shoot-out.
     */
    static List<Double> forSnapshot(long t, int period, int homeGoals, int awayGoals, int homeRed, int awayRed,
                                    double homeXg, double awayXg, boolean women) {
        if (period < 1 || period > 4) return null;
        double played = minutesPlayed(t, period);
        return outcome(homeGoals - awayGoals, remainingMinutes(t, period), homeRed, shrunkPer90(homeXg, played),
                awayRed, shrunkPer90(awayXg, played), women, period >= 3);
    }
}
