package baritone.api;

public interface IBaritone {
    BuilderProcess getBuilderProcess();
    ExploreProcess getExploreProcess();

    interface BuilderProcess {
        void clearArea(net.minecraft.util.math.BlockPos corner1, net.minecraft.util.math.BlockPos corner2);
    }

    interface ExploreProcess {
        void explore(int x, int z);
    }
}
