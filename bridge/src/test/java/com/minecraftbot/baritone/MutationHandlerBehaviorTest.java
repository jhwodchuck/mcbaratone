package com.minecraftbot.baritone;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.core.BlockPos;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.EntityEquipment;
import net.minecraft.world.inventory.ContainerData;
import net.minecraft.world.inventory.FurnaceMenu;
import net.minecraft.world.inventory.SimpleContainerData;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.Level;
import net.minecraft.world.item.crafting.RecipeAccess;
import net.minecraft.world.item.crafting.RecipePropertySet;
import net.minecraft.world.level.material.Fluids;
import net.minecraft.world.phys.Vec3;
import net.minecraft.network.chat.Component;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Handler-level contract tests for mutation commands.
 *
 * These tests intentionally stop before a real world mutation when validating
 * rejection paths. A command being accepted by the bridge is not evidence of
 * a Minecraft effect; the corresponding positive effects still require a
 * disposable-world acceptance test.
 */
class MutationHandlerBehaviorTest {
    @BeforeAll
    static void bootstrap() {
        // Bootstrap must run before touching Blocks/BuiltInRegistries. The
        // older helper probes Blocks first, which initializes registries before
        // Bootstrap and leaves this JVM unable to run handler tests.
        net.minecraft.SharedConstants.tryDetectVersion();
        net.minecraft.server.Bootstrap.bootStrap();
    }

    private static Minecraft client(ClientLevel level, LocalPlayer player, MultiPlayerGameMode gameMode) {
        Minecraft client = mock(Minecraft.class);
        TestUtils.setField(client, "level", level);
        TestUtils.setField(client, "player", player);
        TestUtils.setField(client, "gameMode", gameMode);
        doAnswer(invocation -> {
            invocation.<Runnable>getArgument(0).run();
            return null;
        }).when(client).execute(any(Runnable.class));
        return client;
    }

    private static JsonObject coords(int x, int y, int z) {
        JsonObject params = new JsonObject();
        params.addProperty("x", x);
        params.addProperty("y", y);
        params.addProperty("z", z);
        return params;
    }

    @Test
    void placeBlockRejectsRequestedBlockThatDoesNotMatchHeldItem() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        BlockPos target = new BlockPos(3, 64, 0);
        ItemStack held = mock(ItemStack.class);
        BlockItem stone = (BlockItem) Blocks.STONE.asItem();
        when(held.getItem()).thenReturn(stone);
        when(player.getMainHandItem()).thenReturn(held);

        JsonObject params = coords(3, 64, 0);
        params.addProperty("block", "minecraft:dirt");
        CommandResult result = new PlaceBlockCommandHandler()
            .execute(params, client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("does not match main hand"));
        verifyNoInteractions(gameMode);
        verifyNoInteractions(level);
    }

    @Test
    void placeBlockReportsMinecraftRejectionInsteadOfAcceptedSuccess() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        BlockPos target = new BlockPos(3, 64, 0);
        when(level.getGameTime()).thenReturn(1L);
        when(level.getBlockState(target)).thenReturn(Blocks.AIR.defaultBlockState());
        when(level.getBlockState(target.below())).thenReturn(Blocks.STONE.defaultBlockState());
        ItemStack held = mock(ItemStack.class);
        BlockItem stone = (BlockItem) Blocks.STONE.asItem();
        when(held.getItem()).thenReturn(stone);
        when(held.getHoverName()).thenReturn(Component.literal("stone"));
        when(player.getMainHandItem()).thenReturn(held);
        when(player.getEyePosition()).thenReturn(new Vec3(3.5, 65.5, 2.0));
        when(player.blockInteractionRange()).thenReturn(6.0);
        when(gameMode.useItemOn(any(), any(), any())).thenReturn(InteractionResult.PASS);

        CommandResult result = new PlaceBlockCommandHandler()
            .execute(coords(3, 64, 0), client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Placement rejected by Minecraft"));
        verify(gameMode).useItemOn(any(), any(), any());
    }

    @Test
    void placeFireRejectsAirTargetWithoutSolidSupport() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        BlockPos target = new BlockPos(10, 64, 0);
        when(level.getBlockState(any(BlockPos.class))).thenReturn(Blocks.AIR.defaultBlockState());
        when(player.blockPosition()).thenReturn(new BlockPos(0, 64, 0));
        Inventory inventory = mock(Inventory.class);
        ItemStack flint = mock(ItemStack.class);
        when(flint.getItem()).thenReturn(Items.FLINT_AND_STEEL);
        when(flint.getDamageValue()).thenReturn(0);
        when(flint.getMaxDamage()).thenReturn(64);
        when(inventory.getItem(anyInt())).thenReturn(flint);
        when(player.getInventory()).thenReturn(inventory);
        TestUtils.setField(player, "connection", mock(ClientPacketListener.class));
        when(player.getEyePosition()).thenReturn(new Vec3(10.5, 65.5, 2.0));
        when(player.blockInteractionRange()).thenReturn(8.0);

        CommandResult result = new PlaceFireCommandHandler()
            .execute(coords(10, 64, 0), client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("support"), result.getErrorMessage());
        verifyNoInteractions(gameMode);
    }

    @Test
    void useBucketRejectsWrongHandBeforeRaycastOrUse() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        ItemStack held = mock(ItemStack.class);
        when(player.getMainHandItem()).thenReturn(held);
        when(held.is(Items.BUCKET)).thenReturn(false);
        when(held.is(Items.WATER_BUCKET)).thenReturn(false);

        JsonObject params = coords(0, 64, 0);
        params.addProperty("operation", "pickup");
        CommandResult result = new UseBucketCommandHandler()
            .execute(params, client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Wrong bucket in main hand"));
        verifyNoInteractions(level);
        verifyNoInteractions(gameMode);
    }

    @Test
    void useBucketRejectsPlacementWithoutAVisibleSupportFace() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        BlockPos target = new BlockPos(0, 64, 0);
        ItemStack held = mock(ItemStack.class);
        when(player.getMainHandItem()).thenReturn(held);
        when(held.is(Items.WATER_BUCKET)).thenReturn(true);
        when(player.getEyePosition()).thenReturn(new Vec3(0.5, 65.5, 2.0));
        when(player.blockInteractionRange()).thenReturn(6.0);
        when(level.getFluidState(target)).thenReturn(Fluids.EMPTY.defaultFluidState());
        when(level.getBlockState(any(BlockPos.class))).thenReturn(Blocks.AIR.defaultBlockState());

        JsonObject params = coords(0, 64, 0);
        params.addProperty("operation", "place");
        CommandResult result = new UseBucketCommandHandler()
            .execute(params, client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("visible reachable bucket target"));
        verifyNoInteractions(gameMode);
    }

    @Test
    void smeltItemsRejectsNonFurnaceMenuBeforeMovingItems() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        TestUtils.setField(player, "containerMenu", null);
        JsonObject params = coords(0, 0, 0);
        params.addProperty("input_slot", 3);

        CommandResult result = new SmeltItemsCommandHandler()
            .execute(params, client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Not in a furnace screen"));
        verifyNoInteractions(gameMode);
    }

    @Test
    void inventoryClickRejectsStaleContainerSyncBeforeDispatch() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        Level playerLevel = mock(Level.class);
        RecipeAccess recipes = mock(RecipeAccess.class);
        when(player.level()).thenReturn(playerLevel);
        when(playerLevel.recipeAccess()).thenReturn(recipes);
        when(recipes.propertySet(any())).thenReturn(RecipePropertySet.EMPTY);
        TestUtils.setField(player, "level", playerLevel);
        Inventory inventory = new Inventory(player, new EntityEquipment());
        FurnaceMenu menu = new FurnaceMenu(42, inventory, new SimpleContainer(3), new SimpleContainerData(4));
        TestUtils.setField(player, "containerMenu", menu);
        JsonObject params = new JsonObject();
        params.addProperty("slot", 3);
        params.addProperty("sync_id", 41);

        CommandResult result = new InventoryClickCommandHandler()
            .handle(params, client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Stale sync_id"));
        verifyNoInteractions(gameMode);
    }

    @Test
    void inventoryClickAcceptedWithoutObservedMenuChangeIsUnknown() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        when(level.getGameTime()).thenReturn(1L);
        Level playerLevel = mock(Level.class);
        RecipeAccess recipes = mock(RecipeAccess.class);
        when(player.level()).thenReturn(playerLevel);
        when(playerLevel.recipeAccess()).thenReturn(recipes);
        when(recipes.propertySet(any())).thenReturn(RecipePropertySet.EMPTY);
        Inventory inventory = new Inventory(player, new EntityEquipment());
        FurnaceMenu menu = new FurnaceMenu(42, inventory, new SimpleContainer(3), new SimpleContainerData(4));
        TestUtils.setField(player, "containerMenu", menu);
        JsonObject params = new JsonObject();
        params.addProperty("slot", 3);

        CommandResult result = new InventoryClickCommandHandler()
            .handle(params, client, mock(IBaritone.class), mock(Socket.class))
            .get(4, TimeUnit.SECONDS);

        assertFalse(result.isSuccess());
        assertEquals("unknown", result.getData().get("action_status").getAsString());
        assertFalse(result.getData().get("postcondition_verified").getAsBoolean());
        verify(gameMode).handleContainerInput(eq(42), eq(3), eq(0), any(), eq(player));
    }

    @Test
    void smeltItemsRejectsStaleFurnaceSyncBeforeTransfer() throws Exception {
        ClientLevel level = mock(ClientLevel.class);
        LocalPlayer player = mock(LocalPlayer.class);
        MultiPlayerGameMode gameMode = mock(MultiPlayerGameMode.class);
        Minecraft client = client(level, player, gameMode);
        Level playerLevel = mock(Level.class);
        RecipeAccess recipes = mock(RecipeAccess.class);
        when(player.level()).thenReturn(playerLevel);
        when(playerLevel.recipeAccess()).thenReturn(recipes);
        when(recipes.propertySet(any())).thenReturn(RecipePropertySet.EMPTY);
        TestUtils.setField(player, "level", playerLevel);
        Inventory inventory = new Inventory(player, new EntityEquipment());
        FurnaceMenu menu = new FurnaceMenu(42, inventory, new SimpleContainer(3), new SimpleContainerData(4));
        TestUtils.setField(player, "containerMenu", menu);
        JsonObject params = new JsonObject();
        params.addProperty("input_slot", 3);
        params.addProperty("sync_id", 41);

        CommandResult result = new SmeltItemsCommandHandler()
            .execute(params, client, mock(IBaritone.class), mock(Socket.class)).get();

        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Stale furnace sync_id"));
        verifyNoInteractions(gameMode);
    }
}
