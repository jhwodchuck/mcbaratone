package com.minecraftbot.baritone;

import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Progressive, survival-mode block breaking that bypasses Baritone.
 *
 * Both {@code break_block} (Baritone builder process, {@code clearArea}) and
 * {@code mine}/{@code tunnel} (Baritone MineProcess) refuse to break the block
 * directly beneath the player's feet as a blanket self-preservation rule.
 * That makes a bot pillared on a 1-wide isolated column impossible to free
 * from the Python client: it cannot descend to reach stone. {@code attack_block}
 * is only a single discrete swing and never accumulates breaking progress
 * (verified live: 40 calls, zero effect).
 *
 * This controller reproduces exactly what holding left-click does: each client
 * tick it aims at the target, calls the interaction manager's progressive
 * break, and swings the hand, until the block is gone or a tick budget expires.
 * A command sets the target from any thread (fields are synchronized); the
 * actual Minecraft interaction runs only from {@code tick}, which is invoked on
 * the client thread by ClientTickHandler.
 */
public final class ManualMiningController {

    private static final Logger LOGGER = LoggerFactory.getLogger("baritone-manual-mining");
    private static final ManualMiningController INSTANCE = new ManualMiningController();

    public static ManualMiningController getInstance() {
        return INSTANCE;
    }

    private BlockPos target;
    private Direction face = Direction.UP;
    private int ticksRemaining;
    private boolean started;
    private FastBreakMode fastBreakMode = FastBreakMode.OFF;

    private ManualMiningController() {
    }

    public synchronized void startMining(int x, int y, int z, String faceName, int maxTicks) {
        this.target = new BlockPos(x, y, z);
        this.face = parseFace(faceName);
        this.ticksRemaining = Math.max(1, maxTicks);
        this.started = false;
        LOGGER.info("Manual mining requested at ({}, {}, {}) for up to {} ticks", x, y, z, this.ticksRemaining);
    }

    public synchronized void stop() {
        this.target = null;
        this.ticksRemaining = 0;
        this.started = false;
    }

    public synchronized void configureMode(FastBreakMode mode) {
        this.fastBreakMode = mode == null ? FastBreakMode.OFF : mode;
        LOGGER.info("Fast-break mode set to {}", this.fastBreakMode.wireName());
    }

    public synchronized FastBreakMode getMode() {
        return fastBreakMode;
    }

    public synchronized boolean isActive() {
        return target != null;
    }

    /** Advance the current break by one tick. Runs on the client thread. */
    public synchronized void tick(Minecraft client) {
        applyFastBreak(client);

        if (target == null) {
            return;
        }
        if (client.player == null || client.level == null || client.gameMode == null) {
            stop();
            return;
        }

        // Already gone (broken by us, or by anything else).
        if (client.level.getBlockState(target).isAir()) {
            stop();
            return;
        }

        if (ticksRemaining-- <= 0) {
            LOGGER.info("Manual mining tick budget exhausted at {}", target);
            stop();
            return;
        }

        // Aim at the block centre so the interaction manager's reach/raycast
        // checks accept the target (mining requires facing the block).
        aimAt(client, target);

        if (!started) {
            client.gameMode.startDestroyBlock(target, face);
            started = true;
        } else {
            client.gameMode.continueDestroyBlock(target, face);
        }
        client.player.swing(InteractionHand.MAIN_HAND);
    }

    /**
     * Apply the two behaviours used by Wurst's FastBreak implementation.
     * Legit mode only removes Minecraft's delay between consecutive blocks.
     * Aggressive mode additionally asks the server to finish the active block
     * before the normal client-side progress reaches one. The server remains
     * authoritative, so callers must still verify that the block became air.
     */
    private void applyFastBreak(Minecraft client) {
        if (fastBreakMode == FastBreakMode.OFF || client == null || client.gameMode == null) {
            return;
        }

        MultiPlayerGameMode gameMode = client.gameMode;
        gameMode.destroyDelay = 0;
        if (
            fastBreakMode != FastBreakMode.AGGRESSIVE
                || !gameMode.isDestroying
                || gameMode.destroyBlockPos == null
                || gameMode.destroyProgress >= 1.0F
                || client.level == null
        ) {
            return;
        }

        BlockPos breaking = gameMode.destroyBlockPos;
        if (
            client.level.getBlockState(breaking).isAir()
                || client.level.getBlockState(breaking).getDestroySpeed(client.level, breaking) < 0.0F
        ) {
            return;
        }

        ClientPacketListener connection = client.getConnection();
        if (connection == null) {
            return;
        }
        Direction breakingFace = target != null && target.equals(breaking) ? face : Direction.UP;
        connection.send(
            new ServerboundPlayerActionPacket(
                ServerboundPlayerActionPacket.Action.STOP_DESTROY_BLOCK,
                breaking,
                breakingFace
            )
        );
    }

    private void aimAt(Minecraft client, BlockPos pos) {
        Vec3 eye = client.player.getEyePosition();
        double dx = (pos.getX() + 0.5) - eye.x;
        double dy = (pos.getY() + 0.5) - eye.y;
        double dz = (pos.getZ() + 0.5) - eye.z;
        double horizontal = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        float pitch = (float) (-Math.toDegrees(Math.atan2(dy, horizontal)));
        client.player.setYRot(yaw);
        client.player.setXRot(pitch);
    }

    private Direction parseFace(String name) {
        if (name == null) {
            return Direction.UP;
        }
        try {
            return Direction.valueOf(name.toUpperCase());
        } catch (IllegalArgumentException e) {
            return Direction.UP;
        }
    }
}
