package com.goebl.david;

/**
 * Minimal placeholder stub for com.goebl.david.Webb used during local builds.
 * This is not a real HTTP client; it only provides a tiny surface to satisfy
 * compile-time references in the bridge until the proper dependency can be
 * fetched from Maven Central or replaced with a vendored jar.
 */
public class Webb {
    public Webb() {}

    public static Webb create() {
        return new Webb();
    }

    public Webb at(String url) {
        return this;
    }

    public Webb header(String name, String value) {
        return this;
    }

    public Webb body(Object body) {
        return this;
    }

    public Webb post() {
        return this;
    }

    public Webb ensureSuccess() {
        return this;
    }
}
