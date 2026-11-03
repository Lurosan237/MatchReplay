package io.github.lurosan237.matchreplay;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPInputStream;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.CloseableIterator;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * The Flink job, fed a real match exactly as a chaotic feed delivers it (5% late by up to 8 s, 1%
 * duplicated), must produce the same snapshots as the Python reference computes from the events in
 * perfect order. Fixtures: python -m matchreplay.fixtures.
 */
class ParityTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    static List<String> lines(String name) throws Exception {
        try (InputStream raw = ParityTest.class.getResourceAsStream("/fixtures/" + name);
             BufferedReader r = new BufferedReader(new InputStreamReader(new GZIPInputStream(raw),
                     StandardCharsets.UTF_8))) {
            List<String> out = new ArrayList<>();
            for (String line; (line = r.readLine()) != null; ) {
                out.add(line);
            }
            return out;
        }
    }

    /** Runs the job on a local Flink cluster; returns the snapshots, or the late events if late is true. */
    static List<String> run(List<MatchEvent> events, long maxDelayMs, boolean late) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);
        DataStream<MatchEvent> stream = env.fromData(events);
        SingleOutputStreamOperator<String> state = ReplayJob.liveState(stream, maxDelayMs);
        DataStream<String> wanted = late ? state.getSideOutput(LiveMatchState.LATE) : state;
        List<String> out = new ArrayList<>();
        try (CloseableIterator<String> it = wanted.executeAndCollect()) {
            it.forEachRemaining(out::add);
        }
        return out;
    }

    static List<MatchEvent> input(long matchId) throws Exception {
        List<MatchEvent> events = new ArrayList<>();
        for (String line : lines(matchId + ".input.jsonl.gz")) {
            events.add(ReplayJob.parse(line));
        }
        return events;
    }

    static void assertSameJson(JsonNode expected, JsonNode actual, String where) {
        if (expected.isNumber() && (expected.isFloatingPointNumber() || actual.isFloatingPointNumber())) {
            assertEquals(expected.asDouble(), actual.asDouble(), 1.5e-4, where);
            return;
        }
        if (expected.isContainerNode()) {
            assertEquals(expected.size(), actual.size(), where + " size");
            if (expected.isArray()) {
                for (int i = 0; i < expected.size(); i++) {
                    assertSameJson(expected.get(i), actual.get(i), where + "[" + i + "]");
                }
            } else {
                Iterator<Map.Entry<String, JsonNode>> fields = expected.fields();
                while (fields.hasNext()) {
                    Map.Entry<String, JsonNode> f = fields.next();
                    assertTrue(actual.has(f.getKey()), where + " missing " + f.getKey());
                    assertSameJson(f.getValue(), actual.get(f.getKey()), where + "." + f.getKey());
                }
            }
            return;
        }
        assertEquals(expected, actual, where);
    }

    @ParameterizedTest
    @ValueSource(longs = {3922238L, 3922242L})
    void flinkMatchesTheReference(long matchId) throws Exception {
        List<String> expected = lines(matchId + ".expected.jsonl.gz");
        List<String> actual = run(input(matchId), 10_000, false);
        assertEquals(expected.size(), actual.size(), "number of snapshots");
        for (int i = 0; i < expected.size(); i++) {
            assertSameJson(JSON.readTree(expected.get(i)), JSON.readTree(actual.get(i)), "snapshot " + i);
        }
    }

    @org.junit.jupiter.api.Test
    void matchesAreIndependentOfEachOther() throws Exception {
        // One match after another, then two at once: every match starts at minute 0, and neither
        // may make the other's events look late
        List<MatchEvent> a = input(3922238L);
        List<MatchEvent> b = input(3922242L);
        List<MatchEvent> sequential = new ArrayList<>(a);
        sequential.addAll(b);
        List<MatchEvent> interleaved = new ArrayList<>();
        for (int i = 0; i < Math.max(a.size(), b.size()); i++) {
            if (i < a.size()) interleaved.add(a.get(i));
            if (i < b.size()) interleaved.add(b.get(i));
        }
        for (List<MatchEvent> events : List.of(sequential, interleaved)) {
            List<String> actual = run(events, 10_000, false);
            for (long matchId : new long[] {3922238L, 3922242L}) {
                List<String> expected = lines(matchId + ".expected.jsonl.gz");
                List<String> mine = new ArrayList<>();
                for (String json : actual) {
                    if (JSON.readTree(json).get("match_id").asLong() == matchId) mine.add(json);
                }
                assertEquals(expected.size(), mine.size(), "snapshots of " + matchId);
                for (int i = 0; i < expected.size(); i++) {
                    assertSameJson(JSON.readTree(expected.get(i)), JSON.readTree(mine.get(i)), matchId + " snapshot " + i);
                }
            }
        }
    }

    @org.junit.jupiter.api.Test
    void eventsLaterThanAllowedGoToTheSideOutput() throws Exception {
        // The fixture delays some events by up to 8 s; allowing only 1 s makes some of them late
        List<String> late = run(input(3922238L), 1_000, true);
        assertTrue(late.size() > 0, "expected late events with a 1 s allowance");
        for (String json : late) {
            assertTrue(JSON.readTree(json).has("event_id"));
        }
    }
}
