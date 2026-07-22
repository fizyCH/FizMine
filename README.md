# FizMine Panel

Мощная панель управления Minecraft-сервером на Python/Flask.

## Скриншоты

<img src="assets/Screen1.jpg" width="800">

<img src="assets/Screen2.jpg" width="800">

<img src="assets/Screen3.jpg" width="800">

## Быстрая установка

### Linux

```bash
curl -sLO https://raw.githubusercontent.com/fizyCH/FizMine/main/install.sh && bash install.sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/fizyCH/FizMine/main/install.ps1 | iex
```

### Ручная установка

1. Скачать с [Releases](https://github.com/fizyCH/FizMine/releases)
2. Распаковать в папку сервера
3. Запустить: `python panel.py`

## Требования

- Python 3.7+
- Java 17+ (для Minecraft сервера)

## Управление

```bash
./ctl.sh start      # Запуск
./ctl.sh stop       # Остановка
./ctl.sh restart    # Перезапуск
./ctl.sh status     # Статус
./ctl.sh log        # Логи
```

## Возможности

| Функция | Описание |
|---------|----------|
| Авторизация | Вход с защитой от подбора пароля |
| Анти-брутфорс | 5 неудачных попыток = 5 минут блокировка |
| Память/Диск/CPU | Использование в %; кольцевые диаграммы |
| Файловый менеджер | Загрузка, скачивание, редактирование, удаление |
| Поиск | Рекурсивный поиск по подпапкам |
| Замена ядра | Vanilla, Purpur, Fabric, Arclight |
| Кастомизация | Цвет акцента, прозрачность, светлячки |
| 5 языков | EN, RU, DE, FR, ZH |
| Бэкапы | Панель + сервер |
| Редактор файлов | .json, .yml, .txt, .properties |
| Плагины и Моды | Загрузка, удаление, поиск |
| Проверка обновлений | Сравнение с GitHub, автоустановка |
| Кросс-платформа | Linux + Windows |
