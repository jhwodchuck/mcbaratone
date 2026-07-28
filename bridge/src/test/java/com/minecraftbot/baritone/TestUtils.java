package com.minecraftbot.baritone;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;

public class TestUtils {

    /**
     * Sets a field value on a target object using reflection.
     * Useful for setting public fields on Mockito mocks where when().thenReturn() cannot be used.
     */
    public static void setField(Object target, String fieldName, Object value) {
        try {
            Class<?> clazz = target.getClass();
            // Handle Mockito mocks (which are subclasses)
            if (clazz.getName().contains("MockitoMock")) {
                clazz = clazz.getSuperclass();
            }
            
            // Iterate up class hierarchy to find field
            Field field = null;
            while (clazz != null) {
                try {
                    field = clazz.getDeclaredField(fieldName);
                    break;
                } catch (NoSuchFieldException e) {
                    clazz = clazz.getSuperclass();
                }
            }
            
            if (field == null) {
                throw new RuntimeException("Field '" + fieldName + "' not found on " + target.getClass());
            }

            field.setAccessible(true);
            
            // Remove final modifier if present (not always needed but safe)
            /* 
               Note: Removing final modifier via reflection is tricky in newer Java versions.
               Mockito mocks usually don't have this issue if we are setting fields on the instance.
               We'll try simple set first.
            */
            
            field.set(target, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field '" + fieldName + "'", e);
        }
    }

    /**
     * Initializes Minecraft bootstrap if not already initialized.
     * Prevents NoClassDefFoundError when accessing Blocks, Items, etc.
     */
    public static void initializeBootstrap() {
        try {
            // Check if already initialized (by checking a registry)
            // Or just call it, it should be idempotent or throw.
            // But Bootstrap.initialize() calls SharedConstants.createGameVersion()
            
            // Try accessing Blocks.AIR first
            try {
                Object air = net.minecraft.world.level.block.Blocks.AIR;
                return; // Already initialized
            } catch (Throwable t) {
                // Not initialized
            }

            net.minecraft.server.Bootstrap.bootStrap();
        } catch (Exception e) {
            System.err.println("Failed to initialize Minecraft Bootstrap: " + e.getMessage());
            // It might fail if assets are missing, but for unit tests we assume it works or we mock Registries.
            // If it fails, we might need to mock Registries manually or avoid using Blocks.
        }
    }
}
