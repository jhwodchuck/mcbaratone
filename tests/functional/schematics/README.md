# Schematics Directory

This directory contains `.litematic` schematic files used by T1002B and other litematic build tests.

## Setup Requirement

Baritone's `#build` command looks for litematic files in your **Minecraft instance's schematics folder**, not directly from this repository.

To run T1002B successfully, copy the litematic file to your Minecraft schematics folder:

```powershell
Copy-Item tests\functional\schematics\StarterWoodenHouse.litematic -Destination "$env:APPDATA\.minecraft\schematics\"
```

Or for MultiMC/Prism instances:
```powershell
Copy-Item tests\functional\schematics\StarterWoodenHouse.litematic -Destination "C:\Minecraft\MultiMC\instances\<your-instance>\.minecraft\schematics\"
```

## Files

| File | Dimensions | Used By |
|------|------------|---------|
| `StarterWoodenHouse.litematic` | 25x26x23 | T1002B |
