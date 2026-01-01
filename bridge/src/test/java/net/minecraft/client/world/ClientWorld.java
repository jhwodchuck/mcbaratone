package net.minecraft.client.world;

public class ClientWorld {
    public RegistryKey getRegistryKey() {
        return new RegistryKey();
    }

    public static class RegistryKey {
        public Identifier getValue() {
            return new Identifier();
        }
    }

    public static class Identifier {
        @Override
        public String toString() {
            return "minecraft:overworld";
        }
    }
}
