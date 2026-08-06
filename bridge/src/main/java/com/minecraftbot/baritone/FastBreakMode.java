package com.minecraftbot.baritone;

import java.util.Locale;

/** Mining acceleration modes exposed by the bridge. */
public enum FastBreakMode {
    OFF,
    LEGIT,
    AGGRESSIVE;

    /** Parse a bridge value without silently accepting an unsafe typo. */
    public static FastBreakMode parse(String value) {
        if (value == null) {
            throw new IllegalArgumentException("Fast-break mode is required");
        }
        try {
            return FastBreakMode.valueOf(value.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException(
                "Unknown fast-break mode: " + value + " (expected off, legit, or aggressive)"
            );
        }
    }

    public String wireName() {
        return name().toLowerCase(Locale.ROOT);
    }
}
