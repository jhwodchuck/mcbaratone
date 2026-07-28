package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EntityTypes;
import net.minecraft.world.entity.npc.villager.Villager;
import org.junit.jupiter.api.Disabled;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@Disabled("Requires Minecraft Bootstrap (Entity class initialization) which fails in unit test environment")
class EntityInteractionCommandHandlerTest {

    @Mock
    private Minecraft mockClient;

    @Mock
    private IBaritone mockBaritone;

    private EntityInteractionCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new EntityInteractionCommandHandler();
    }

    @Test
    void testCommandName() {
        assertEquals("entity_interact", handler.getCommandName());
    }

    @Test
    void testAttackEntity_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 123);
        params.addProperty("action", "attack");

        Entity mockEntity = mock(Entity.class);
        when(mockEntity.getX()).thenReturn(0.0);
        when(mockEntity.getY()).thenReturn(0.0);
        when(mockEntity.getZ()).thenReturn(0.0);
        when(mockClient.level.getEntity(123)).thenReturn(mockEntity);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals(123, result.getData().get("entity_id").getAsInt());
        assertEquals("attack", result.getData().get("action").getAsString());
    }

    @Test
    void testAttackEntity_EntityNotFound() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 999);
        params.addProperty("action", "attack");

        when(mockClient.level.getEntity(999)).thenReturn(null);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Entity not found: 999", result.getErrorMessage());
    }

    @Test
    void testTameAnimal_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 456);
        params.addProperty("action", "tame");

        net.minecraft.world.entity.TamableAnimal mockAnimal = mock(
                net.minecraft.world.entity.TamableAnimal.class);
        when(mockAnimal.getX()).thenReturn(0.0);
        when(mockAnimal.getY()).thenReturn(0.0);
        when(mockAnimal.getZ()).thenReturn(0.0);
        when(mockAnimal.isTame()).thenReturn(false);
        when(mockAnimal.getType()).thenReturn((EntityType) EntityTypes.WOLF);

        when(mockClient.level.getEntity(456)).thenReturn(mockAnimal);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("tame", result.getData().get("action").getAsString());
    }

    @Test
    void testTameAnimal_AlreadyTamed() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 456);
        params.addProperty("action", "tame");

        net.minecraft.world.entity.TamableAnimal mockAnimal = mock(
                net.minecraft.world.entity.TamableAnimal.class);
        when(mockAnimal.getX()).thenReturn(0.0);
        when(mockAnimal.getY()).thenReturn(0.0);
        when(mockAnimal.getZ()).thenReturn(0.0);
        when(mockAnimal.isTame()).thenReturn(true);

        when(mockClient.level.getEntity(456)).thenReturn(mockAnimal);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Entity already tamed", result.getErrorMessage());
    }

    @Test
    void testTradeVillager_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 789);
        params.addProperty("action", "trade");

        Villager mockVillager = mock(Villager.class);
        when(mockVillager.getX()).thenReturn(0.0);
        when(mockVillager.getY()).thenReturn(0.0);
        when(mockVillager.getZ()).thenReturn(0.0);
        when(mockVillager.getType()).thenReturn((EntityType) EntityTypes.VILLAGER);

        when(mockClient.level.getEntity(789)).thenReturn(mockVillager);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("trade", result.getData().get("action").getAsString());
    }

    @Test
    void testLookAtEntity_Success() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 321);
        params.addProperty("action", "look_at");

        Entity mockEntity = mock(Entity.class);
        when(mockEntity.getX()).thenReturn(10.0);
        when(mockEntity.getY()).thenReturn(10.0);
        when(mockEntity.getZ()).thenReturn(10.0);
        when(mockEntity.getId()).thenReturn(321);

        when(mockClient.level.getEntity(321)).thenReturn(mockEntity);
        when(mockClient.player.getX()).thenReturn(0.0);
        when(mockClient.player.getY()).thenReturn(0.0);
        when(mockClient.player.getZ()).thenReturn(0.0);
        when(mockClient.player.getEyeHeight(any())).thenReturn(1.62f);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals(321, result.getData().get("target_entity_id").getAsInt());
    }

    @Test
    void testLookAtEntity_ByType() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_type", "minecraft:villager");
        params.addProperty("action", "look_at");

        Villager mockVillager = mock(Villager.class);
        when(mockVillager.getX()).thenReturn(5.0);
        when(mockVillager.getY()).thenReturn(5.0);
        when(mockVillager.getZ()).thenReturn(5.0);
        when(mockVillager.getId()).thenReturn(555);

        when(mockClient.level.entitiesForRendering()).thenReturn(java.util.List.of(mockVillager));
        when(mockClient.player.getX()).thenReturn(0.0);
        when(mockClient.player.getY()).thenReturn(0.0);
        when(mockClient.player.getZ()).thenReturn(0.0);
        when(mockClient.player.getEyeHeight(any())).thenReturn(1.62f);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals(555, result.getData().get("target_entity_id").getAsInt());
    }

    @Test
    void testEntityTooFar() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 123);
        params.addProperty("action", "attack");

        Entity mockEntity = mock(Entity.class);
        when(mockEntity.getX()).thenReturn(100.0);
        when(mockEntity.getY()).thenReturn(100.0);
        when(mockEntity.getZ()).thenReturn(100.0);
        when(mockClient.level.getEntity(123)).thenReturn(mockEntity);
        when(mockClient.player.getX()).thenReturn(0.0);
        when(mockClient.player.getY()).thenReturn(0.0);
        when(mockClient.player.getZ()).thenReturn(0.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Entity too far"));
    }

    @Test
    void testDetectInteractions() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("action", "detect");

        Entity mockCow = mock(Entity.class);
        when(mockCow.getX()).thenReturn(2.0);
        when(mockCow.getY()).thenReturn(2.0);
        when(mockCow.getZ()).thenReturn(2.0);
        when(mockCow.getId()).thenReturn(100);
        when(mockCow.getType()).thenReturn((EntityType) EntityTypes.COW);

        when(mockClient.level.entitiesForRendering()).thenReturn(java.util.List.of(mockCow));
        when(mockClient.player.getX()).thenReturn(0.0);
        when(mockClient.player.getY()).thenReturn(0.0);
        when(mockClient.player.getZ()).thenReturn(0.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("available_interactions"));
    }

    @Test
    void testTameDifferentAnimals_Cat() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 200);
        params.addProperty("action", "tame");

        net.minecraft.world.entity.TamableAnimal mockCat = mock(net.minecraft.world.entity.TamableAnimal.class);
        when(mockCat.getX()).thenReturn(0.0);
        when(mockCat.getY()).thenReturn(0.0);
        when(mockCat.getZ()).thenReturn(0.0);
        when(mockCat.isTame()).thenReturn(false);
        when(mockCat.getType()).thenReturn((EntityType) EntityTypes.CAT);

        when(mockClient.level.getEntity(200)).thenReturn(mockCat);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("tame", result.getData().get("action").getAsString());
    }

    @Test
    void testTameDifferentAnimals_Horse() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 300);
        params.addProperty("action", "tame");

        net.minecraft.world.entity.TamableAnimal mockHorse = mock(net.minecraft.world.entity.TamableAnimal.class);
        when(mockHorse.getX()).thenReturn(0.0);
        when(mockHorse.getY()).thenReturn(0.0);
        when(mockHorse.getZ()).thenReturn(0.0);
        when(mockHorse.isTame()).thenReturn(false);
        when(mockHorse.getType()).thenReturn((EntityType) EntityTypes.HORSE);

        when(mockClient.level.getEntity(300)).thenReturn(mockHorse);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("tame", result.getData().get("action").getAsString());
    }

    @Test
    void testTameDifferentAnimals_Parrot() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 400);
        params.addProperty("action", "tame");

        net.minecraft.world.entity.TamableAnimal mockParrot = mock(
                net.minecraft.world.entity.TamableAnimal.class);
        when(mockParrot.getX()).thenReturn(0.0);
        when(mockParrot.getY()).thenReturn(0.0);
        when(mockParrot.getZ()).thenReturn(0.0);
        when(mockParrot.isTame()).thenReturn(false);
        when(mockParrot.getType()).thenReturn((EntityType) EntityTypes.PARROT);

        when(mockClient.level.getEntity(400)).thenReturn(mockParrot);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("tame", result.getData().get("action").getAsString());
    }

    @Test
    void testShearCommands_WithValidation_Sheep() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 500);
        params.addProperty("action", "shear");

        net.minecraft.world.entity.animal.sheep.Sheep mockSheep = mock(net.minecraft.world.entity.animal.sheep.Sheep.class);
        when(mockSheep.getX()).thenReturn(0.0);
        when(mockSheep.getY()).thenReturn(0.0);
        when(mockSheep.getZ()).thenReturn(0.0);
        when(mockSheep.readyForShearing()).thenReturn(true);
        when(mockSheep.getType()).thenReturn((EntityType) EntityTypes.SHEEP);

        when(mockClient.level.getEntity(500)).thenReturn(mockSheep);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("shear", result.getData().get("action").getAsString());
        assertTrue(result.getData().get("sheared").getAsBoolean());
    }

    @Test
    void testShearCommands_WithValidation_SheepNotShearable() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 501);
        params.addProperty("action", "shear");

        net.minecraft.world.entity.animal.sheep.Sheep mockSheep = mock(net.minecraft.world.entity.animal.sheep.Sheep.class);
        when(mockSheep.getX()).thenReturn(0.0);
        when(mockSheep.getY()).thenReturn(0.0);
        when(mockSheep.getZ()).thenReturn(0.0);
        when(mockSheep.readyForShearing()).thenReturn(false); // Baby sheep or already sheared
        when(mockSheep.getType()).thenReturn((EntityType) EntityTypes.SHEEP);

        when(mockClient.level.getEntity(501)).thenReturn(mockSheep);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Entity is not shearable", result.getErrorMessage());
    }

    @Test
    void testMilkCommands_Cow() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 600);
        params.addProperty("action", "milk");

        net.minecraft.world.entity.animal.cow.Cow mockCow = mock(net.minecraft.world.entity.animal.cow.Cow.class);
        when(mockCow.getX()).thenReturn(0.0);
        when(mockCow.getY()).thenReturn(0.0);
        when(mockCow.getZ()).thenReturn(0.0);
        when(mockCow.getType()).thenReturn((EntityType) EntityTypes.COW);

        when(mockClient.level.getEntity(600)).thenReturn(mockCow);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("milk", result.getData().get("action").getAsString());
        assertTrue(result.getData().get("milked").getAsBoolean());
    }

    @Test
    void testMilkCommands_Mooshroom() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 700);
        params.addProperty("action", "milk");

        net.minecraft.world.entity.animal.cow.MushroomCow mockMooshroom = mock(
                net.minecraft.world.entity.animal.cow.MushroomCow.class);
        when(mockMooshroom.getX()).thenReturn(0.0);
        when(mockMooshroom.getY()).thenReturn(0.0);
        when(mockMooshroom.getZ()).thenReturn(0.0);
        when(mockMooshroom.getType()).thenReturn((EntityType) EntityTypes.MOOSHROOM);

        when(mockClient.level.getEntity(700)).thenReturn(mockMooshroom);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals("milk", result.getData().get("action").getAsString());
        assertTrue(result.getData().get("milked").getAsBoolean());
    }

    @Test
    void testMilkCommands_InvalidTarget() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 800);
        params.addProperty("action", "milk");

        Entity mockChicken = mock(Entity.class);
        when(mockChicken.getX()).thenReturn(0.0);
        when(mockChicken.getY()).thenReturn(0.0);
        when(mockChicken.getZ()).thenReturn(0.0);
        when(mockChicken.getType()).thenReturn((EntityType) EntityTypes.CHICKEN);

        when(mockClient.level.getEntity(800)).thenReturn(mockChicken);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Entity cannot be milked", result.getErrorMessage());
    }

    @Test
    void testTargetValidation_InvalidEntityType() throws ExecutionException, InterruptedException {
        // Setup
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 900);
        params.addProperty("action", "tame");

        Entity mockZombie = mock(Entity.class);
        when(mockZombie.getX()).thenReturn(0.0);
        when(mockZombie.getY()).thenReturn(0.0);
        when(mockZombie.getZ()).thenReturn(0.0);
        when(mockZombie.getType()).thenReturn((EntityType) EntityTypes.ZOMBIE);

        when(mockClient.level.getEntity(900)).thenReturn(mockZombie);
        when(mockClient.player.getX()).thenReturn(1.0);
        when(mockClient.player.getY()).thenReturn(1.0);
        when(mockClient.player.getZ()).thenReturn(1.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertEquals("Entity type not supported for this action", result.getErrorMessage());
    }

    @Test
    void testDistanceLimits_TooFar() throws ExecutionException, InterruptedException {
        // Setup - Entity very far away
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 123);
        params.addProperty("action", "attack");

        Entity mockEntity = mock(Entity.class);
        when(mockEntity.getX()).thenReturn(1000.0);
        when(mockEntity.getY()).thenReturn(1000.0);
        when(mockEntity.getZ()).thenReturn(1000.0);
        when(mockClient.level.getEntity(123)).thenReturn(mockEntity);
        when(mockClient.player.getX()).thenReturn(0.0);
        when(mockClient.player.getY()).thenReturn(0.0);
        when(mockClient.player.getZ()).thenReturn(0.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertFalse(result.isSuccess());
        assertTrue(result.getErrorMessage().contains("Entity too far"));
    }

    @Test
    void testDistanceLimits_WithinRange() throws ExecutionException, InterruptedException {
        // Setup - Entity within interaction range
        JsonObject params = new JsonObject();
        params.addProperty("entity_id", 124);
        params.addProperty("action", "attack");

        Entity mockEntity = mock(Entity.class);
        when(mockEntity.getX()).thenReturn(3.0);
        when(mockEntity.getY()).thenReturn(3.0);
        when(mockEntity.getZ()).thenReturn(3.0);
        when(mockClient.level.getEntity(124)).thenReturn(mockEntity);
        when(mockClient.player.getX()).thenReturn(0.0);
        when(mockClient.player.getY()).thenReturn(0.0);
        when(mockClient.player.getZ()).thenReturn(0.0);

        // Execute
        CompletableFuture<CommandResult> future = handler.handle(params, mockClient, mockBaritone, null);
        CommandResult result = future.get();

        // Verify
        assertTrue(result.isSuccess());
        assertEquals(124, result.getData().get("entity_id").getAsInt());
    }
}
