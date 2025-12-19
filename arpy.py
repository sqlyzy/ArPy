#!/usr/bin/env python3

import zipfile
import sys
import os
import json
import importlib.abc
import importlib.machinery
import importlib.util
import types
import argparse
from pathlib import Path
from io import BytesIO
import hashlib
from datetime import datetime


class ArpyManifest:
    
    def __init__(self, name: str = "unnamed", version: str = "1.0.0", 
                 main_module: str = "__main__", author: str = "", 
                 description: str = ""):
        self.data = {
            "arpy_version": "1.0.0",
            "name": name,
            "version": version,
            "main_module": main_module,
            "author": author,
            "description": description,
            "created": datetime.now().isoformat(),
            "python_requires": f">={sys.version_info.major}.{sys.version_info.minor}",
            "modules": [],
            "checksum": ""
        }
    
    def to_json(self) -> str:
        return json.dumps(self.data, indent=2, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'ArpyManifest':
        manifest = cls()
        manifest.data = json.loads(json_str)
        return manifest


class ArpyBuilder:
    
    def __init__(self, source_dir: str, output_file: str = None):
        self.source_dir = Path(source_dir).resolve()
        self.output_file = output_file or f"{self.source_dir.name}.arpy"
        self.files_added = []
        
    def build(self, name: str = None, version: str = "1.0.0", 
              main_module: str = "__main__", author: str = "", 
              description: str = "") -> str:
        
        if not self.source_dir.exists():
            raise FileNotFoundError(f"Директория не найдена: {self.source_dir}")
        
        name = name or self.source_dir.name
        
        manifest = ArpyManifest(
            name=name,
            version=version,
            main_module=main_module,
            author=author,
            description=description
        )
        
        with zipfile.ZipFile(self.output_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            
            for py_file in self.source_dir.rglob("*.py"):
                rel_path = py_file.relative_to(self.source_dir)
                arcname = str(rel_path).replace('\\', '/')
                
                if "__pycache__" in arcname:
                    continue
                
                content = py_file.read_bytes()
                zf.writestr(arcname, content)
                
                self.files_added.append(arcname)
                manifest.data["modules"].append(arcname)
            
            for pattern in ["*.json", "*.yaml", "*.yml", "*.txt", "*.cfg"]:
                for file in self.source_dir.rglob(pattern):
                    rel_path = file.relative_to(self.source_dir)
                    arcname = str(rel_path).replace('\\', '/')
                    
                    if "__pycache__" not in arcname:
                        content = file.read_bytes()
                        zf.writestr(arcname, content)
            
            manifest.data["checksum"] = self._calculate_checksum(zf)
            zf.writestr("META-INF/manifest.json", manifest.to_json())
        
        return self.output_file
    
    def _calculate_checksum(self, zf: zipfile.ZipFile) -> str:
        hasher = hashlib.sha256()
        for name in sorted(zf.namelist()):
            hasher.update(name.encode())
            hasher.update(zf.read(name))
        return hasher.hexdigest()[:16]


class ArpyLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    
    def __init__(self, arpy_path: str):
        self.arpy_path = Path(arpy_path).resolve()
        self.zf = zipfile.ZipFile(self.arpy_path, 'r')
        self.manifest = self._load_manifest()
        self._module_cache = {}
        self._build_module_index()
        
    def _load_manifest(self) -> ArpyManifest:
        try:
            manifest_data = self.zf.read("META-INF/manifest.json").decode('utf-8')
            return ArpyManifest.from_json(manifest_data)
        except KeyError:
            return ArpyManifest()
    
    def _build_module_index(self):
        self._modules = {}
        self._packages = set()
        
        for name in self.zf.namelist():
            if name.endswith('.py') and not name.startswith('META-INF'):
                module_name = name[:-3].replace('/', '.').replace('\\', '.')
                
                if module_name.endswith('.__init__'):
                    package_name = module_name[:-9]
                    self._modules[package_name] = name
                    self._packages.add(package_name)
                else:
                    self._modules[module_name] = name
                    
                parts = module_name.split('.')
                for i in range(len(parts) - 1):
                    parent = '.'.join(parts[:i+1])
                    self._packages.add(parent)
    
    def find_module(self, fullname: str, path=None):
        if fullname in self._modules or fullname in self._packages:
            return self
        return None
    
    def find_spec(self, fullname: str, path=None, target=None):
        if fullname in self._modules:
            is_package = fullname in self._packages
            return importlib.machinery.ModuleSpec(
                fullname,
                self,
                is_package=is_package,
                origin=f"arpy://{self.arpy_path}#{fullname}"
            )
        elif fullname in self._packages:
            return importlib.machinery.ModuleSpec(
                fullname,
                self,
                is_package=True,
                origin=f"arpy://{self.arpy_path}#{fullname}"
            )
        return None
    
    def create_module(self, spec):
        return None
    
    def exec_module(self, module):
        fullname = module.__name__
        
        if fullname in self._modules:
            filename = self._modules[fullname]
        elif fullname in self._packages:
            filename = f"{fullname.replace('.', '/')}/__init__.py"
            if filename not in self.zf.namelist():
                module.__path__ = []
                return
        else:
            raise ImportError(f"Модуль {fullname} не найден в {self.arpy_path}")
        
        source = self.zf.read(filename).decode('utf-8')
        code = compile(source, f"arpy://{self.arpy_path}/{filename}", 'exec')
        
        module.__file__ = f"arpy://{self.arpy_path}/{filename}"
        module.__loader__ = self
        
        if fullname in self._packages:
            module.__path__ = [str(self.arpy_path)]
        
        exec(code, module.__dict__)
    
    def get_source(self, fullname: str) -> str:
        if fullname in self._modules:
            return self.zf.read(self._modules[fullname]).decode('utf-8')
        return None
    
    def install(self):
        if self not in sys.meta_path:
            sys.meta_path.insert(0, self)
        return self
    
    def uninstall(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)
    
    def close(self):
        self.uninstall()
        self.zf.close()
    
    def __enter__(self):
        return self.install()
    
    def __exit__(self, *args):
        self.close()


class ArpyRunner:
    
    @staticmethod
    def run(arpy_path: str, args: list = None):
        args = args or []
        arpy_path = Path(arpy_path).resolve()
        
        if not arpy_path.exists():
            raise FileNotFoundError(f"Файл не найден: {arpy_path}")
        
        loader = ArpyLoader(arpy_path)
        loader.install()
        
        try:
            main_module = loader.manifest.data.get("main_module", "__main__")
            
            old_argv = sys.argv.copy()
            sys.argv = [str(arpy_path)] + args
            
            if main_module in loader._modules:
                source = loader.get_source(main_module)
                code = compile(source, f"arpy://{arpy_path}/{main_module}.py", 'exec')
                
                main_globals = {
                    '__name__': '__main__',
                    '__file__': str(arpy_path),
                    '__loader__': loader,
                    '__builtins__': __builtins__,
                }
                
                exec(code, main_globals)
            else:
                raise ImportError(f"Главный модуль '{main_module}' не найден в архиве")
                
        finally:
            sys.argv = old_argv
            loader.close()
    
    @staticmethod
    def info(arpy_path: str) -> dict:
        with ArpyLoader(arpy_path) as loader:
            return {
                "manifest": loader.manifest.data,
                "modules": list(loader._modules.keys()),
                "packages": list(loader._packages),
                "files": loader.zf.namelist()
            }


def extract_arpy(arpy_path: str, output_dir: str = None):
    arpy_path = Path(arpy_path)
    output_dir = Path(output_dir or arpy_path.stem)
    
    with zipfile.ZipFile(arpy_path, 'r') as zf:
        zf.extractall(output_dir)
    
    print(f"✓ Распаковано в: {output_dir}")
    return output_dir


def list_arpy(arpy_path: str):
    info = ArpyRunner.info(arpy_path)
    
    print(f"\n📦 {Path(arpy_path).name}")
    print("=" * 50)
    
    manifest = info["manifest"]
    print(f"  Имя:        {manifest.get('name', 'N/A')}")
    print(f"  Версия:     {manifest.get('version', 'N/A')}")
    print(f"  Автор:      {manifest.get('author', 'N/A') or 'Не указан'}")
    print(f"  Описание:   {manifest.get('description', 'N/A') or 'Нет'}")
    print(f"  Создан:     {manifest.get('created', 'N/A')}")
    print(f"  Main:       {manifest.get('main_module', '__main__')}")
    print(f"  Checksum:   {manifest.get('checksum', 'N/A')}")
    
    print(f"\n📁 Модули ({len(info['modules'])}):")
    for mod in sorted(info['modules']):
        print(f"    • {mod}")
    
    print(f"\n📂 Пакеты ({len(info['packages'])}):")
    for pkg in sorted(info['packages']):
        print(f"    • {pkg}")


def interactive_mode():
    print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║     █████╗ ██████╗ ██████╗ ██╗   ██╗                    ║
║    ██╔══██╗██╔══██╗██╔══██╗╚██╗ ██╔╝                    ║
║    ███████║██████╔╝██████╔╝ ╚████╔╝                     ║
║    ██╔══██║██╔══██╗██╔═══╝   ╚██╔╝                      ║
║    ██║  ██║██║  ██║██║        ██║                       ║
║    ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝        ╚═╝                       ║
║                                                          ║
║         Archive Python - аналог JAR для Python           ║
║                      Версия 1.0.0                        ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    while True:
        print("\n┌─────────────── МЕНЮ ───────────────┐")
        print("│                                    │")
        print("│  1. 📦 Упаковать проект в .arpy    │")
        print("│  2. ▶️  Запустить .arpy файл        │")
        print("│  3. 📋 Показать содержимое .arpy   │")
        print("│  4. 📂 Распаковать .arpy           │")
        print("│  5. ❓ Помощь                       │")
        print("│  0. 🚪 Выход                        │")
        print("│                                    │")
        print("└────────────────────────────────────┘")
        
        choice = input("\n➤ Выберите действие (0-5): ").strip()
        
        if choice == "1":
            build_interactive()
        elif choice == "2":
            run_interactive()
        elif choice == "3":
            list_interactive()
        elif choice == "4":
            extract_interactive()
        elif choice == "5":
            show_help()
        elif choice == "0":
            print("\n👋 До свидания!")
            break
        else:
            print("❌ Неверный выбор. Попробуйте снова.")


def build_interactive():
    print("\n" + "="*50)
    print("📦 УПАКОВКА ПРОЕКТА")
    print("="*50)
    
    source = input("\n📁 Путь к папке проекта: ").strip()
    if not source:
        print("❌ Путь не указан!")
        return
    
    source = source.strip('"').strip("'")
    
    if not os.path.exists(source):
        print(f"❌ Папка не найдена: {source}")
        return
    
    default_name = os.path.basename(source)
    name = input(f"📝 Имя проекта [{default_name}]: ").strip() or default_name
    version = input("🔢 Версия [1.0.0]: ").strip() or "1.0.0"
    author = input("👤 Автор: ").strip()
    description = input("📄 Описание: ").strip()
    main_module = input("🎯 Главный модуль [__main__]: ").strip() or "__main__"
    
    default_output = f"{name}.arpy"
    output = input(f"💾 Сохранить как [{default_output}]: ").strip() or default_output
    
    try:
        print("\n⏳ Упаковка...")
        builder = ArpyBuilder(source, output)
        result = builder.build(
            name=name,
            version=version,
            main_module=main_module,
            author=author,
            description=description
        )
        print(f"\n✅ Успешно создан: {result}")
        print(f"   Упаковано файлов: {len(builder.files_added)}")
        
        for f in builder.files_added:
            print(f"   • {f}")
            
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    
    input("\n⏎ Нажмите Enter для продолжения...")


def run_interactive():
    print("\n" + "="*50)
    print("▶️  ЗАПУСК .ARPY")
    print("="*50)
    
    arpy_files = list(Path(".").glob("*.arpy"))
    
    if arpy_files:
        print("\n📂 Найденные .arpy файлы:")
        for i, f in enumerate(arpy_files, 1):
            print(f"   {i}. {f.name}")
    
    arpy_file = input("\n📦 Файл .arpy (имя или номер): ").strip()
    
    if arpy_file.isdigit():
        idx = int(arpy_file) - 1
        if 0 <= idx < len(arpy_files):
            arpy_file = str(arpy_files[idx])
        else:
            print("❌ Неверный номер!")
            return
    
    arpy_file = arpy_file.strip('"').strip("'")
    
    if not arpy_file.endswith('.arpy'):
        arpy_file += '.arpy'
    
    if not os.path.exists(arpy_file):
        print(f"❌ Файл не найден: {arpy_file}")
        input("\n⏎ Нажмите Enter для продолжения...")
        return
    
    args_str = input("📝 Аргументы (через пробел, можно пропустить): ").strip()
    args = args_str.split() if args_str else []
    
    print("\n" + "─"*50)
    print("🚀 ЗАПУСК...")
    print("─"*50 + "\n")
    
    try:
        ArpyRunner.run(arpy_file, args)
    except Exception as e:
        print(f"\n❌ Ошибка выполнения: {e}")
    
    print("\n" + "─"*50)
    input("⏎ Нажмите Enter для продолжения...")


def list_interactive():
    print("\n" + "="*50)
    print("📋 СОДЕРЖИМОЕ .ARPY")
    print("="*50)
    
    arpy_files = list(Path(".").glob("*.arpy"))
    
    if arpy_files:
        print("\n📂 Найденные .arpy файлы:")
        for i, f in enumerate(arpy_files, 1):
            print(f"   {i}. {f.name}")
    
    arpy_file = input("\n📦 Файл .arpy (имя или номер): ").strip()
    
    if arpy_file.isdigit():
        idx = int(arpy_file) - 1
        if 0 <= idx < len(arpy_files):
            arpy_file = str(arpy_files[idx])
        else:
            print("❌ Неверный номер!")
            return
    
    arpy_file = arpy_file.strip('"').strip("'")
    
    if not arpy_file.endswith('.arpy'):
        arpy_file += '.arpy'
    
    if not os.path.exists(arpy_file):
        print(f"❌ Файл не найден: {arpy_file}")
        input("\n⏎ Нажмите Enter для продолжения...")
        return
    
    try:
        list_arpy(arpy_file)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    
    input("\n⏎ Нажмите Enter для продолжения...")


def extract_interactive():
    print("\n" + "="*50)
    print("📂 РАСПАКОВКА .ARPY")
    print("="*50)
    
    arpy_files = list(Path(".").glob("*.arpy"))
    
    if arpy_files:
        print("\n📂 Найденные .arpy файлы:")
        for i, f in enumerate(arpy_files, 1):
            print(f"   {i}. {f.name}")
    
    arpy_file = input("\n📦 Файл .arpy (имя или номер): ").strip()
    
    if arpy_file.isdigit():
        idx = int(arpy_file) - 1
        if 0 <= idx < len(arpy_files):
            arpy_file = str(arpy_files[idx])
        else:
            print("❌ Неверный номер!")
            return
    
    arpy_file = arpy_file.strip('"').strip("'")
    
    if not arpy_file.endswith('.arpy'):
        arpy_file += '.arpy'
    
    if not os.path.exists(arpy_file):
        print(f"❌ Файл не найден: {arpy_file}")
        input("\n⏎ Нажмите Enter для продолжения...")
        return
    
    default_output = Path(arpy_file).stem + "_extracted"
    output_dir = input(f"📁 Папка для распаковки [{default_output}]: ").strip() or default_output
    
    try:
        extract_arpy(arpy_file, output_dir)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    
    input("\n⏎ Нажмите Enter для продолжения...")


def show_help():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                         СПРАВКА                              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  ARPY - это формат упаковки Python проектов в один файл.    ║
║  Аналог JAR в Java.                                          ║
║                                                              ║
║  ▸ .arpy файл - это ZIP архив с Python кодом                ║
║  ▸ Содержит манифест с метаданными                          ║
║  ▸ Можно запускать напрямую                                 ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  КАК ИСПОЛЬЗОВАТЬ:                                           ║
║                                                              ║
║  1. Создайте проект с файлом __main__.py                    ║
║  2. Упакуйте его в .arpy (пункт 1 меню)                     ║
║  3. Запустите .arpy файл (пункт 2 меню)                     ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  СТРУКТУРА ПРОЕКТА:                                          ║
║                                                              ║
║  myproject/                                                  ║
║  ├── __main__.py    ← точка входа (обязательно!)            ║
║  ├── core/                                                   ║
║  │   ├── __init__.py                                        ║
║  │   └── engine.py                                          ║
║  └── utils/                                                  ║
║      └── helpers.py                                          ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  КОМАНДНАЯ СТРОКА:                                           ║
║                                                              ║
║  python arpy.py build ./myproject -o app.arpy               ║
║  python arpy.py run app.arpy                                ║
║  python arpy.py list app.arpy                               ║
║  python arpy.py extract app.arpy -o ./output                ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    input("\n⏎ Нажмите Enter для продолжения...")


def main():
    parser = argparse.ArgumentParser(
        description="ARPY - Archive Python (аналог JAR для Python)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  arpy build ./myproject -o myapp.arpy
  arpy run myapp.arpy
  arpy list myapp.arpy
  arpy extract myapp.arpy -o ./output
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Команды')
    
    build_parser = subparsers.add_parser('build', help='Упаковать проект в .arpy')
    build_parser.add_argument('source', help='Директория проекта')
    build_parser.add_argument('-o', '--output', help='Выходной файл')
    build_parser.add_argument('-n', '--name', help='Имя проекта')
    build_parser.add_argument('-v', '--version', default='1.0.0', help='Версия')
    build_parser.add_argument('-m', '--main', default='__main__', help='Главный модуль')
    build_parser.add_argument('-a', '--author', default='', help='Автор')
    build_parser.add_argument('-d', '--description', default='', help='Описание')
    
    run_parser = subparsers.add_parser('run', help='Запустить .arpy файл')
    run_parser.add_argument('arpy_file', help='Файл .arpy')
    run_parser.add_argument('args', nargs='*', help='Аргументы программы')
    
    list_parser = subparsers.add_parser('list', help='Показать содержимое .arpy')
    list_parser.add_argument('arpy_file', help='Файл .arpy')
    
    extract_parser = subparsers.add_parser('extract', help='Распаковать .arpy')
    extract_parser.add_argument('arpy_file', help='Файл .arpy')
    extract_parser.add_argument('-o', '--output', help='Директория для распаковки')
    
    args = parser.parse_args()
    
    if args.command is None:
        interactive_mode()
        return
    
    if args.command == 'build':
        builder = ArpyBuilder(args.source, args.output)
        output = builder.build(
            name=args.name,
            version=args.version,
            main_module=args.main,
            author=args.author,
            description=args.description
        )
        print(f"✅ Создан: {output}")
        print(f"   Файлов упаковано: {len(builder.files_added)}")
        
    elif args.command == 'run':
        ArpyRunner.run(args.arpy_file, args.args)
        
    elif args.command == 'list':
        list_arpy(args.arpy_file)
        
    elif args.command == 'extract':
        extract_arpy(args.arpy_file, args.output)


if __name__ == '__main__':
    main()
