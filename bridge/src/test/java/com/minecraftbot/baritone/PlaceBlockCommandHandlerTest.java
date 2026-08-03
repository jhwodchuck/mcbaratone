package com.minecraftbot.baritone;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;

import net.minecraft.core.Direction;
import org.junit.jupiter.api.Test;

class PlaceBlockCommandHandlerTest {

    @Test
    void overheadSupportIsOnlyUsedAfterReachableSideFaces() {
        assertArrayEquals(
            new Direction[] {
                Direction.DOWN,
                Direction.NORTH,
                Direction.SOUTH,
                Direction.WEST,
                Direction.EAST,
                Direction.UP
            },
            PlaceBlockCommandHandler.PLACEMENT_SUPPORT_ORDER
        );
    }
}
