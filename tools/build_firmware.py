"""Reproducible local build using the CubeIDE ARM GCC toolchain (no board access)."""
from pathlib import Path
import argparse
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]

def build(tool_bin, project):
    src = ROOT / project
    out = ROOT / 'build' / project
    out.mkdir(parents=True, exist_ok=True)
    gcc = str(tool_bin / 'arm-none-eabi-gcc.exe')
    includes = [src / p for p in ('Core/Inc', 'Drivers/STM32F0xx_HAL_Driver/Inc',
                'Drivers/STM32F0xx_HAL_Driver/Inc/Legacy', 'Drivers/CMSIS/Device/ST/STM32F0xx/Include',
                'Drivers/CMSIS/Include')]
    flags = ['-mcpu=cortex-m0', '-mthumb', '-mfloat-abi=soft', '-std=gnu11', '-Os', '-g3',
             '-DUSE_HAL_DRIVER', '-DSTM32F030x8', '-ffunction-sections', '-fdata-sections',
             '-Wall', '-Wextra', '--specs=nano.specs'] + ['-I' + str(p) for p in includes]
    sources = sorted((src / 'Core/Src').glob('*.c'))
    sources += sorted((src / 'Core/Startup').glob('*.s'))
    sources += [p for p in sorted((src / 'Drivers/STM32F0xx_HAL_Driver/Src').glob('*.c'))
                if not p.name.endswith('_template.c')]
    objects = []
    for source in sources:
        obj = out / (source.stem + '.o')
        subprocess.run([gcc, *flags, '-c', str(source), '-o', str(obj)], check=True)
        objects.append(str(obj))
    elf = out / (project + '.elf')
    subprocess.run([gcc, *flags, '-T' + str(src / 'STM32F030R8TX_FLASH.ld'),
                    '--specs=nosys.specs', '-Wl,--gc-sections', '-Wl,-Map=' + str(out / (project + '.map')),
                    *objects, '-Wl,--start-group', '-lc', '-lm', '-Wl,--end-group', '-o', str(elf)], check=True)
    subprocess.run([str(tool_bin / 'arm-none-eabi-objcopy.exe'), '-O', 'binary', str(elf), str(elf.with_suffix('.bin'))], check=True)
    subprocess.run([str(tool_bin / 'arm-none-eabi-size.exe'), str(elf)], check=True)
    size = elf.with_suffix('.bin').stat().st_size
    limit = 13 * 1024 if project == 'bootloader_stm32' else 24 * 1024
    assert size <= limit, (project, size, limit)
    if project == 'application_stm32':
        import importlib.util
        spec = importlib.util.spec_from_file_location('firmware_protocol', ROOT / 'Bootloader GUI/protocol.py')
        protocol = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(protocol)
        protocol.validate_firmware(elf.with_suffix('.bin').read_bytes())
    print(f'{project}: binary {size}/{limit} bytes', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tool-bin', type=Path)
    args = parser.parse_args()
    candidates = list(Path('C:/ST').glob('**/tools/bin/arm-none-eabi-gcc.exe')) if not args.tool_bin else []
    tool_bin = args.tool_bin or (candidates[0].parent if candidates else None)
    if tool_bin is None:
        parser.error('Pass --tool-bin PATH to the CubeIDE ARM GCC bin directory')
    for project in ('bootloader_stm32', 'application_stm32'):
        build(tool_bin, project)
