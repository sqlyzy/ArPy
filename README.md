# ArPy
ArPy - Это аналог .jar в python! Позволит вам объединять большие проекты в один .arpy файл!

# CMD
📦 Упаковать проект в .arpy
python arpy.py build ./myproject -o myapp.arpy

▶️ Запустить .arpy файл
python arpy.py run myapp.arpy

📋 Показать содержимое .arpy
python arpy.py list myapp.arpy

📂 Распаковать .arpy в папку
python arpy.py extract myapp.arpy -o ./output

# Опции build
python arpy.py build ./myproject \
    -o myapp.arpy \          ^.arpy файл^
    -n "My App" \            ^название^
    -v "2.0.0" \             ^версия^
    -a "Автор" \             ^автор^
    -d "Описание проекта" \  ^описание^
    -m "__main__"            ^главный файл .py^
