"""Default scenario. IDs are stable; customer-facing copy is editable in the UI."""
from copy import deepcopy


def choice(key, label, **kw):
    return {"id": key, "label": label, **kw}


def options(labels):
    return [choice(str(i), label) for i, label in enumerate(labels)]


OTHER = choice("other", "Другое — уточню", detail="Опишите свой вариант")
YESNO = [choice("yes", "Да"), choice("no", "Нет")]
SPHERES = options([
    "Строительство / благоустройство", "Ремонтные работы",
    "Производство / промышленность", "Автомобили",
    "Специалисты / услуги (бьюти, консультации, юристы)",
    "Еда / доставка / кулинария", "Спорт / отдых / тренеры",
]) + [OTHER]
SECTIONS = {
    "basic": {"title": "Основные страницы", "options": options([
        "О компании / Обо мне", "Контакты", "Прайс / цены", "Акции",
        "Доставка и оплата", "FAQ — Часто задаваемые вопросы"]) + [OTHER]},
    "catalog": {"title": "Услуги и товары", "options": options([
        "Услуги", "Товары (каталог)", "Сертификаты", "Бренды / марки", "Вакансии"]) + [OTHER]},
    "trust": {"title": "Доверие и отзывы", "options": options([
        "Преимущества", "Отзывы", "Партнёры / сотрудничество", "Новости / статьи"]) + [OTHER]},
    "projects": {"title": "Портфолио и проекты", "options": options([
        "Наши проекты", "Работы / объекты", "Фотогалерея"]) + [OTHER]},
    "legal": {"title": "Правовая информация", "options": options([
        "Политика конфиденциальности", "Пользовательское соглашение", "Договор-оферта"])},
}


def build_config():
    n = {}

    def add(key, title, kind="single", next=None, opts=None, **kw):
        n[key] = {"id": key, "title": title, "kind": kind, "next": next,
                  "options": deepcopy(opts or []), **kw}

    add("kind", "Что вы хотите разместить на сайте?", opts=[
        choice("goods", "Товары", next="payment"),
        choice("services", "Услуги", next="sphere"),
        choice("both", "Товары и услуги", next="priority"),
        choice("other", "Иное — расскажу", detail="Что вы хотите разместить на сайте?", next="design"),
    ])
    add("priority", "Что будет приоритетом?", next="sphere", opts=options([
        "Каталог товаров", "Каталог услуг", "Примерно поровну", "Не знаю — нужна помощь"]))
    add("sphere", "В какой сфере вы работаете?", next="@after_sphere", opts=SPHERES)
    add("structure", "Какую структуру сайта вы рассматриваете?", next="content", opts=options([
        "Одностраничный сайт (лендинг)", "Многостраничный сайт", "Зависит от бюджета",
        "Затрудняюсь, нужна консультация"]))
    add("content", "У вас есть наполнение для сайта?", "multi", "payment", options([
        "Есть все тексты и материалы", "Есть частично, нужна помощь в подборе",
        "Нужны уникальные тексты", "Требуется подбор изображений", "Затрудняюсь, нужно обсуждение"]) + [OTHER])
    add("payment", "Нужно ли подключать оплату на сайте?", next="@after_payment", opts=YESNO,
        hint="Подключение онлайн-оплаты — только для ИП и юридических лиц.")
    add("delivery", "Нужен ли расчёт стоимости доставки?", next="stock", opts=YESNO,
        hint="СДЭК, ПЭК, BoxBerry, Почта РФ и другие службы.")
    add("stock", "Нужна интеграция с учётом остатков?", next="integration", opts=YESNO,
        hint="Например, 1С или МойСклад.")
    add("integration", "Нужна интеграция с другими сервисами?", next="@after_integration", opts=YESNO,
        hint="CRM, онлайн-чаты и другие системы.")
    add("features", "Какой функционал нужен?", "multi", "variants", options([
        "Подписка на рассылку", "Купоны / промокоды", "Личный кабинет", "Купить в 1 клик",
        "Поиск", "Квиз", "Онлайн-консультант", "Калькулятор", "Выбор города / языка"]) + [OTHER],
        optional=True)
    add("variants", "У товаров будут вариации?", "multi", "goods_count", [
        choice("none", "Нет", exclusive=True)] + options(["Цвет", "Размер", "Вес", "Объём", "Материал"]) + [OTHER])
    add("goods_count", "Сколько товаров планируется?", next="goods_source", opts=options([
        "Менее 100", "100–1 000", "1 000–10 000", "Более 10 000"]))
    add("goods_source", "Как передадите информацию о товарах?", next="sections", opts=options([
        "Заполню Excel", "Уже есть файл", "Нужно парсить", "Пока не знаю"]))
    add("popups", "Нужны ли всплывающие окна (pop-up)?", next="sections", opts=[
        choice("call", "Да, для заявки на обратный звонок"),
        choice("texts", "Да, с текстами / ценами об услугах", detail="Сколько и какие окна вы хотите разместить?"),
        choice("consult", "Нужна консультация: не понимаю, что это"),
        choice("unsure", "Знаю, что это, но не уверен(а), нужны ли они")],
        hint="Окна могут открываться по кнопке «Подробнее» или собирать заявки.")
    add("sections", "Какие разделы сайта вам понадобятся?", "sections", "@after_sections",
        hint="Откройте категории, отметьте страницы и нажмите «Готово».")
    add("design", "Пожелания по стилистике и дизайну?", next="@after_design", opts=options([
        "Полный индивидуальный дизайн + логотип", "Только логотип, остальное предложите вы",
        "Есть логотип, нужен дизайн под него", "Главное — современно и красиво",
        "Шаблон подойдёт, бюджет ограничен", "Хотелось бы проконсультироваться"]))
    add("split", "Нужны ли разные потоки: «купить» и «заказать услугу»?", next="reference", opts=options([
        "Да, две разные страницы", "Нет, всё в одной системе", "Не уверен(а)"]))
    add("reference", "Есть ли пример сайта, который вам нравится?", next="name", opts=[
        choice("yes", "Да, есть", detail="Пришлите ссылку на сайт и комментарий: что именно нравится?", validator="url"),
        choice("help", "Нет, нужна помощь с примерами"),
        choice("file", "Есть подробное ТЗ — прикреплю файл", detail="Прикрепите ТЗ: PDF, DOCX, TXT, XLSX или изображение, до 10 МБ.", validator="file"),
        OTHER])
    add("promote_channels", "Что вы хотите продвигать?", "multi", "promote_types", [
        choice("site", "Сайт", detail="Введите ссылку на сайт", validator="url"),
        choice("social", "Соцсети", detail="Пришлите ссылки на ваши соцсети", validator="url"),
        choice("2gis", "Профиль в 2ГИС", detail="Введите ссылку на профиль в 2ГИС", validator="url"),
        choice("business", "Яндекс Бизнес", detail="Введите ссылку на Яндекс Бизнес", validator="url"),
        choice("maps", "Яндекс Карты", detail="Введите ссылку на Яндекс Карты", validator="url"),
        choice("yservices", "Яндекс Услуги", detail="Введите ссылку на Яндекс Услуги", validator="url"),
        choice("none", "Пока нет сайта и других каналов", exclusive=True),
        choice("consult", "Не знаю, нужна рекомендация", exclusive=True), OTHER],
        hint="Продвижение бизнеса: анализ, настройка и сопровождение. Можно выбрать несколько каналов.")
    add("promote_types", "Какие типы продвижения вам интересны?", "multi", "promote_current", options([
        "Реклама в Яндекс Директ", "Реклама в 2ГИС", "Реклама в Яндекс Бизнес",
        "Реклама в Яндекс Картах", "Реклама в Яндекс Услугах", "Реклама в соцсетях",
        "SEO-продвижение"]) + [choice("consult", "Не знаю, нужна рекомендация", exclusive=True), OTHER])
    add("promote_current", "Сейчас вы уже что-то продвигаете?", "multi", "@after_current", [
        choice("paid", "Да, через платную рекламу", detail="Какую рекламу используете сейчас?"),
        choice("seo", "Да, через SEO-продвижение"), choice("social", "Да, через соцсети"),
        choice("unknown", "Да, но не знаю точно, что это", detail="Опишите, что делаете сейчас"),
        choice("past", "Продвигали раньше, но сейчас нет", detail="Опишите, как продвигали раньше", exclusive=True),
        choice("never", "Никогда не продвигали, нужна помощь", exclusive=True),
        choice("consult", "Затрудняюсь ответить, нужна консультация", exclusive=True)])
    add("promote_helpers", "Вам кто-то уже помогал с продвижением?", "multi", "promote_support", [
        choice("none", "Нет, никогда не обращались", exclusive=True),
        choice("yandex", "Да, помогали сотрудники Яндекс"),
        choice("marketer", "Да, работали с маркетологами / SEO-специалистами"), OTHER])
    add("promote_support", "Вас интересует разовая настройка или сопровождение?", next="geography", opts=options([
        "Настройка и регулярное продвижение", "Только разовая настройка", "Нужна консультация"]) + [OTHER])
    add("geography", "География продвижения", next="goals", opts=options([
        "Один город", "Несколько регионов", "Вся Россия", "Международно"]))
    add("goals", "Какие цели у рекламы?", next="analysis_url", opts=options([
        "Заявки", "Звонки", "Продажи", "Увеличение трафика", "Всё вместе"]))
    add("analysis_url", "Оставьте ссылку на сайт для анализа", "text", "name", validator="url",
        hint="Если сайта пока нет, можно пропустить этот вопрос.", optional=True)
    add("platform", "На какой платформе сделан ваш сайт?", next="improvements", opts=options([
        "WordPress", "Tilda", "OpenCart", "Joomla", "1С-Битрикс"]) + [OTHER,
        choice("consult", "Не знаю, нужна консультация")])
    add("improvements", "Что именно вы хотели бы доработать?", "multi", "current_url", options([
        "Улучшить визуал или сделать редизайн", "Исправить вёрстку, структуру, логику",
        "Исправить баги и нерабочие функции", "Добавить / удалить страницы",
        "Добавить функционал: калькулятор, квиз, формы, корзина",
        "Нужны рекомендации специалиста", "Перенос на другую платформу"]) + [OTHER])
    add("current_url", "Введите ссылку на ваш сайт для анализа", "text", "rebuild", validator="url",
        options=[choice("forgot", "Не помню ссылку, нужна помощь"), choice("offline", "Сайт не открывается, нужна помощь")])
    add("rebuild", "Готовы рассмотреть полную пересборку сайта?", next="current_ads", opts=options([
        "Да, можно предложить и такой вариант", "Нет, нужны только доработки", "Затрудняюсь, нужна консультация"]))
    add("current_ads", "Сейчас сайт продвигается?", "multi", "access", options([
        "Да, реклама в Яндекс Директ", "Да, в Яндекс Бизнес", "Да, SEO-продвижение"]) + [OTHER,
        choice("none", "Нет, пока не продвигаем", exclusive=True)],
        hint="Учитываем продвижение, чтобы сохранить позиции при доработке.")
    add("access", "У вас есть доступы от сайта?", next="name", opts=options([
        "Есть все доступы: домен и хостинг", "Есть только доступ от домена",
        "Есть только доступ от хостинга", "Не помню / потерял доступы, нужна помощь",
        "Нет никаких доступов"]) + [OTHER], hint="Только уточняем наличие. Не присылайте пароли или ключи.")
    add("name", "Как к вам обращаться?", "text", "phone", validator="name",
        hint="Остался последний шаг — контактные данные для обсуждения проекта.")
    add("phone", "Номер телефона для связи", "text", "messengers", validator="phone",
        hint="Не будем названивать — только по делу. Например, +7 999 123-45-67.")
    add("messengers", "Как удобнее связаться?", "multi", "contact_time", options([
        "WhatsApp", "Telegram", "Звонки"]))
    add("contact_time", "Когда вам удобно связаться?", next="review", opts=options([
        "Утро (09:00–12:00)", "День (12:00–17:00)", "Вечер (после 17:00)",
        "Без разницы — пишите в любое время"]), hint="Время по вашему часовому поясу — уточним при связи.")
    return {
        "brand": "SiteBrief", "tagline": "От идеи до понятного проекта",
        "greeting": "Здравствуйте! Чем можем помочь?",
        "privacy_intro": "Перед началом: ответы и контактные данные нужны для подготовки предложения. Они сохраняются у владельца бота и доступны его специалистам. Нажимая «Начать», вы соглашаетесь на такую обработку. Не отправляйте пароли и секретные ключи.",
        "privacy_text": "Это демонстрационная политика. В демо используйте вымышленные данные. Для рабочего запуска владелец должен указать оператора, цели и сроки хранения, контакты для запросов и правовое основание обработки. Данные Telegram обрабатываются также самим Telegram. Запрос удаления данных доступен через меню бота.",
        "thanks": "Спасибо за заявку, {name}! Бриф сохранён. Специалист сможет изучить его и связаться с вами в выбранное время.",
        "contact_text": "Контакты компании пока не настроены. Можно оставить заявку — она появится в панели специалиста.",
        "contact_url": "", "contact_phone": "", "contact_email": "",
        "portfolio_intro": "Каждый сайт создаётся под задачи клиента. Можно адаптировать стиль под вашу нишу.",
        "portfolio": [{"id": str(i), "title": o["label"], "items": []} for i, o in enumerate(SPHERES[:-1])],
        "menu_labels": {"new": "Заказать новый сайт", "improve": "Доработать текущий сайт",
                        "promote": "Продвижение сайта", "portfolio": "Примеры наших работ",
                        "contact": "Связаться с нами"},
        "nodes": n, "sections": deepcopy(SECTIONS),
    }


SERVICE_NAMES = {"new": "Новый сайт", "improve": "Доработка сайта", "promote": "Продвижение"}
STARTS = {"new": "kind", "improve": "platform", "promote": "promote_channels"}
ROUTES = {
    "@after_sphere": lambda a: "structure" if a.get("kind", {}).get("values") == ["services"] else "payment",
    "@after_payment": lambda a: "integration" if a.get("kind", {}).get("values") == ["services"] else "delivery",
    "@after_integration": lambda a: "popups" if a.get("kind", {}).get("values") == ["services"] else "features",
    "@after_sections": lambda a: "reference" if a.get("kind", {}).get("values") == ["services"] else "design",
    "@after_design": lambda a: "split" if a.get("kind", {}).get("values") == ["both"] else "reference",
    "@after_current": lambda a: "name" if set(a.get("promote_current", {}).get("values", [])) & {"never", "consult"} else "promote_helpers",
}


def validate_config(config, baseline=None):
    """Accept copy edits, not executable graph changes or untrusted URL schemes."""
    from urllib.parse import urlsplit
    base = baseline or build_config()
    if set(config) != set(base):
        raise ValueError("Состав настроек не совпадает с версией приложения")
    for key in base:
        if isinstance(base[key], str):
            if not isinstance(config[key], str) or len(config[key]) > 2500:
                raise ValueError(f"Некорректное поле: {key}")
    if not config["brand"].strip() or len(config["brand"]) > 40:
        raise ValueError("Название: от 1 до 40 символов")
    def valid_url(s):
        return not s or (urlsplit(s).scheme == "https" and bool(urlsplit(s).hostname))
    if not valid_url(config["contact_url"]):
        raise ValueError("Для кнопки контакта нужна ссылка https://")
    if set(config["menu_labels"]) != set(base["menu_labels"]):
        raise ValueError("Нельзя менять идентификаторы меню")
    for label in config["menu_labels"].values():
        if not isinstance(label, str) or not 1 <= len(label) <= 60:
            raise ValueError("Название кнопки меню: 1–60 символов")
    if set(config["nodes"]) != set(base["nodes"]):
        raise ValueError("Нельзя удалять обязательные вопросы")
    for key, node in config["nodes"].items():
        original = deepcopy(base["nodes"][key])
        candidate = deepcopy(node)
        for field in ("title", "hint"):
            if field in candidate:
                value = candidate.pop(field)
                if not isinstance(value, str) or not 1 <= len(value) <= 1500:
                    raise ValueError("Текст вопроса: 1–1500 символов")
            original.pop(field, None)
        for c, o in zip(candidate.get("options", []), original.get("options", [])):
            for field in ("label", "detail"):
                if field in c:
                    value = c.pop(field)
                    if not isinstance(value, str) or not 1 <= len(value) <= 100:
                        raise ValueError("Подпись кнопки / уточнения: 1–100 символов")
                o.pop(field, None)
        if candidate != original:
            raise ValueError("Можно менять тексты, но не логику переходов")
    if config["sections"] != base["sections"]:
        raise ValueError("Категории разделов фиксированы сценарием")
    if len(config["portfolio"]) != 7:
        raise ValueError("В портфолио должно быть семь сфер")
    for i, category in enumerate(config["portfolio"]):
        if set(category) != {"id", "title", "items"} or category["id"] != str(i):
            raise ValueError("Неверный формат категории")
        if not isinstance(category["title"], str) or not 1 <= len(category["title"]) <= 100:
            raise ValueError("Название сферы: 1–100 символов")
        if not isinstance(category["items"], list) or len(category["items"]) > 10:
            raise ValueError("Не более 10 работ в категории")
        for item in category["items"]:
            if set(item) != {"title", "url", "image"} or not isinstance(item["title"], str) or not 1 <= len(item["title"]) <= 120:
                raise ValueError("Укажите название работы до 120 символов")
            if not all(isinstance(item[f], str) and len(item[f]) <= 1000 and valid_url(item[f]) for f in ("url", "image")):
                raise ValueError("Ссылки портфолио должны начинаться с https://")
    if len(config["thanks"]) > 1000 or config["thanks"].replace("{name}", "").find("{") >= 0:
        raise ValueError("В благодарности допустима только подстановка {name}")
    return config
