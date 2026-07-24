---
name: fronted-design
description: Ультимативный скилл веб-дизайна — вкус, технологии, доступность, моушн, компоненты, аудит. Fronted-Design: объединение классического вкуса и современного стека 2026.
---

# fronted-design — ультимативный скилл дизайна

> Этот скилл — тренировка вкуса И технологий. Ты создаёшь интерфейсы уровня Apple / Linear / Vercel / Stripe: характерные, доступные, технически современные. Никакого AI-slop. Каждый пиксель осмыслен.

---

## 0. Прежде чем открыть редактор — контракт

Ответь на четыре вопроса. Без ответов — не начинай.

1. **Кто пользователь?** Скучающий админ, торопящийся студент, инвестор за 30 секунд? Дизайн без понимания пользователя — украшение пустоты.
2. **Эстетический якорь.** Выбери КОНКРЕТНЫЙ характер: brutalist editorial / swiss minimal / luxury OLED dark / organic-biomorphic / retro-futur HUD / playful toy-like / industrial utility / soft pastel / magazine maximalism / **street-authentic** (грубый, тактильный, с намёком на физический мир). Половинчатость = AI-slop.

   **Как проверить, что якорь работает:** можешь описать сайт тремя прилагательными? Если да — якорь есть. Если прилагательные generic («современный, минималистичный») — нет.

   **Примеры характеров из практики:**
   — *Street-authentic:* толстая обводка 2px везде, агрессивная тень 8px 8px 0, элементы повёрнуты на 1-2deg, физические стикеры/штампы, бумажная текстура
   — *Editorial premium:* крупная типографика, контрастные размеры, выворотка, magazine-grid, цитаты на полях
   — *Tech-craft:* Unbounded/TASA + Golos, оранжево-чёрный, код-блоки в hero, микровзаимодействия
   — *Dark luxury:* oklch(0.02 0.005 260) вместо #000, тонкая внутренняя обводка, туманные градиенты, медленные анимации

3. **Один незабываемый элемент.** Что человек вспомнит через неделю? Не абстракцию («красивый сайт»), а конкретную деталь:
   — вращающийся SVG-бейдж с текстом по кругу
   — неоновая вывеска с flicker-анимацией
   — floating-частицы (хангыль, иероглифы, геометрия)
   — кастомный курсор с trail
   — 3D-параллакс на hero
   — физический штамп/stamp на угле

   **Требование:** этот элемент должен быть виден за 2 секунды после загрузки. Не прячь его под скролл.

4. **Контекст-метафора.** Тема диктует форму. Сайт про кофе = тёплые оттенки + медленный моушн. Финтех = плотная сетка + моноширинные цифры. Игра = энергия + гипер-контраст. Dev tool = код-блоки + тёмная тема + моноширинный акцент. Еда = тактильность, физические элементы (штампы, стикеры, бумага), пар/туман, тёплый свет.

> Сначала направление, потом код. Inter + фиолетовый градиент = немедленный redo.

---

## Процесс: дизайн-мышление и рефлексия

Прежде чем писать код — войди в состояние дизайнера. Этот раздел про то, **как думать**, а не что делать.

### Рефлексивный цикл

Хороший дизайн рождается из постоянного переформулирования. Не «сделай красиво», а «что здесь важно?». Проходи этот цикл на каждом этапе:

1. **Meaning** — В чём суть? Какое главное сообщение? Что пользователь должен почувствовать/сделать?
2. **Structure** — Как организовать пространство, чтобы смысл читался без усилий?
3. **Typography** — Какой шрифт передаёт характер? Как ритм размеров ведёт взгляд?
4. **Color** — Какая палитра поддерживает настроение, не отвлекая от контента?
5. **Motion** — Как движение направляет внимание и подтверждает действия?
6. **Acceptance** — Всё ли работает вместе? Нет ли лишнего? Честен ли дизайн?

Каждый шаг — вопрос, не ответ. Если на «Meaning» нет чёткого ответа — остановись и выясни.

### Принцип рефлексии

Дизайнер постоянно говорит с собой — это не неуверенность, а метод.

- «Я сосредоточен на том, чтобы каждый элемент начинался с чего-то узнаваемого»
- «Я избегаю шаблонов, излишеств и запрещённых решений, стремясь к точности, ритму и дыханию»
- «Фон всегда живой: тонкий слой, текстура, деталь, которая не мешает контенту, но придаёт глубину»

Веди внутренний монолог. Каждое решение объясни себе: почему этот отступ, этот цвет, эта анимация?

### Что проверять на каждом этапе

| Этап | Вопрос себе |
|------|------------|
| Смысл | Кому это нужно? Что изменится, если это убрать? |
| Структура | Глаз знает, куда смотреть? Есть ли иерархия? |
| Типографика | Текст читается? Заголовки командуют вниманием? |
| Цвет | Палитра поддерживает характер? Нет «случайных» цветов? |
| Движение | Каждая анимация оправдана? Нет лишних шевелений? |
| Приёмка | Это готово к показу реальному пользователю? |

### Fidelity progression

Не пытайся сделать всё сразу. Начинай с крупных блоков, добавляй детали слоями:

1. **Wireframe-уровень** — только структура, отступы, иерархия. Никакого цвета и шрифтов.
2. **Характер** — шрифт, палитра, настроение. Одна итерация на выбор направления.
3. **Детали** — тени, радиусы, переходы, микро-взаимодействия.
4. **Полировка** — контраст, доступность, responsive, крайние состояния.

Каждый слой — отдельный проход рефлексивного цикла.

> Если элемент не служит смыслу — он лишний.

---

## 1. Типографика — основа вкуса

Текст занимает 90% площади интерфейса. Плохой шрифт не спасут никакие тени.

### 1.1 Выбор шрифтов

**Характерный display + спокойный body.**
- Display: выразительный шрифт с характером (serif с контрастом, гротеск с необычной геометрией, моноширинный, гуманистический)
- Body: читаемый, с высокой x-height, проверенный

**Антипаттерн:** Inter / Space Grotesk «по умолчанию» — AI-клише 2024-2026. Если используешь Inter — только осознанно и с характерным display-напарником.

**Рабочие пары 2026:**
| Display | Body | Настроение |
|---------|------|------------|
| GT Sectra | Söhne | Editorial, дорого |
| PP Editorial New | Inter | Сдержанный премиум |
| Tiempos Headline | Founders Grotesk | Журнал, качество |
| GT Walsheim (жирный) | GT Walsheim (regular) | Уверенный тех-бренд |
| Unbounded | Onest | Современный, дерзкий |
| Instrument Serif (free) | Inter (free) | Editorial бюджет |
| Playfair Display (free) | Inter (free) | Классика бюджет |
| DM Serif Display | DM Sans | Editoral, тёплый |
| Fraunces (variable) | Work Sans | Органический, премиум |
| Cabinet Grotesk | Inter | Тех-бренд, строгий |
| Satoshi | Clash Display | Современный модальный |

**Платные foundries** (дают премиум-качество): Pangram Pangram, Sharp Type, Klim, Commercial Type, Grilli Type.

### 1.2 Modular Scale

Размеры — узлы шкалы, а не случайные числа.

- **1.250 (Major Third)** — универсальный старт для большинства сайтов
- **1.333 (Perfect Fourth)** — для editorial / журнального стиля
- **1.200 (Minor Third)** — для плотных интерфейсов (дашборды, админки)

Пример шкалы с base 16px и ratio 1.25:
```
--text-xs:   0.75rem   (12px)
--text-sm:   0.875rem  (14px)
--text-base: 1rem      (16px)   ← body
--text-lg:   1.25rem   (20px)
--text-xl:   1.563rem  (25px)
--text-2xl:  1.953rem  (31px)
--text-3xl:  2.441rem  (39px)
--text-4xl:  3.052rem  (49px)
--text-5xl:  3.815rem  (61px)
--text-6xl:  4.768rem  (76px)
```

### 1.3 Fluid Typography

Каждый размер шкалы — через `clamp()` для адаптива без медиа-запросов:

```css
--text-base: clamp(1rem, 0.9167rem + 0.4167vw, 1.125rem);    /* 16-18px */
--text-4xl:  clamp(2rem, 1rem + 5vw, 3.75rem);               /* 32-60px */
--text-6xl:  clamp(2.5rem, 0.5rem + 8vw, 5rem);              /* 40-80px */
```

Формула: `clamp(min, min + (max - min) * (100vw - minViewport) / (maxViewport - minViewport), max)`

Диапазон: 320px — 1280px по умолчанию.

### 1.4 Настройки строки

```css
--lh-tight:  1.05;    /* h1-h2 */
--lh-normal: 1.15;    /* h3-h4 */
--lh-body:   1.55;    /* p, li */
--lh-loose:  1.65;    /* длинные статьи */

--ls-tight:  -0.03em; /* крупные заголовки >48px */
--ls-normal: -0.02em; /* заголовки */
--ls-body:   0;       /* body text */
--ls-wide:   0.04em;  /* капс, мелкий UI-текст */
```

- **Measure (ширина строки):** body 60-75 символов. Для русского — ближе к 55-65 (кириллица шире).
- **Line-height:** обратно пропорционален размеру. Крупный заголовок: 1.0-1.15. Body: 1.5-1.65. Подписи: 1.4.
- **Letter-spacing:** крупный заголовок — отрицательный (визуально «собирает»). Капс/мелкий — положительный.
- **Числа в данных:** `font-variant-numeric: tabular-nums` — моноширинные цифры.

### 1.5 Техническая настройка шрифтов

```css
/* Self-hosted variable font — пример */
@font-face {
  font-family: 'Geist';
  src: url('/fonts/Geist-Variable.woff2') format('woff2');
  font-display: swap;
  font-weight: 100 900;
  size-adjust: 95%;
  ascent-override: 90%;
  descent-override: 22%;
}

/* Fallback matching через size-adjust — предотвращает CLS */
/* Используй Fontaine или next/font для автоматизации */
```

**Правила:**
- Self-host шрифты (не Google Fonts CDN) — быстрее, надёжнее, приватнее
- `font-display: swap` или `optional` для body (optional — показывает системный, если web-шрифт не загрузился за 100ms)
- Subsetting: только кириллица + латиница + цифры — экономия 50-70% объёма
- Variable fonts: один .woff2 вместо 10-20 статических файлов
- Preload LCP-шрифт: `<link rel="preload" as="font" href="/fonts/...">`

### 1.6 Русская типографика (обязательно)

```css
/* Подключи Типограф Лебедева в пайплайн контента */
/* https://www.artlebedev.ru/typograf/ */
```

- Кавычки: «ёлочки» (французские `chevrons`), вложенные — „лапки“ (немецкие)
- Длинное тире (—, `—`) между частями предложения
- Короткое тире (–, `–`) для диапазонов (10–20)
- Дефис (-) только внутри слов
- Неразрывные пробелы (`&nbsp;`) после предлогов/союзов (в, на, из, и, а, но) и перед длинным тире
- Инструменты: Типограф Лебедева, typograf.js, dzdl.ru

### 1.7 OpenType-фичи

```css
body {
  font-feature-settings: "liga" 1,        /* лигатуры */
                          "kern" 1,        /* кернинг */
                          "tnum" 1,        /* табулярные цифры */
                          "ss01" 1;        /* стилистические альтернативы (если есть) */
}
```

---

## 2. Цвет — система, а не выбор

### 2.1 OKLCH — современный стандарт

Используй `oklch(L C H / a)` вместо HEX/HSL. OKLCH — перцептивно-равномерное пространство: одинаковое L = одинаковая perceived brightness для любого hue.

```css
/* ❌ Плохо — HSL врёт о яркости */
--primary: #3b82f6;

/* ✅ Хорошо — OKLCH даёт предсказуемый контраст */
--primary: oklch(0.55 0.22 250);
--primary-hover: oklch(0.62 0.22 250);
--primary-subtle: oklch(0.55 0.22 250 / 0.12);
```

**Преимущества OKLCH:**
- Одинаковый L = одинаковая perceived яркость для любого hue
- Нативная поддержка P3-гаммы (+30% цвета к sRGB)
- Предсказуемая генерация шкал
- Читаемый синтаксис

### 2.2 Цветовая система (Radix-подход)

Каждый цвет — шкала из 10-12 тонов (50-950). Имена по ролям, не по цвету.

```css
:root {
  /* Neutral scale (12 steps) */
  --neutral-1:  oklch(0.99 0.002 260);
  --neutral-2:  oklch(0.97 0.003 260);
  --neutral-3:  oklch(0.94 0.005 260);
  --neutral-4:  oklch(0.91 0.006 260);
  --neutral-5:  oklch(0.88 0.007 260);
  --neutral-6:  oklch(0.85 0.008 260);
  --neutral-7:  oklch(0.80 0.009 260);
  --neutral-8:  oklch(0.72 0.01 260);
  --neutral-9:  oklch(0.60 0.015 260);
  --neutral-10: oklch(0.48 0.015 260);
  --neutral-11: oklch(0.38 0.015 260);
  --neutral-12: oklch(0.18 0.01 260);

  /* Brand primary (10 steps) */
  --primary-1:  oklch(0.97 0.01 250);
  --primary-2:  oklch(0.93 0.02 250);
  --primary-3:  oklch(0.88 0.04 250);
  --primary-4:  oklch(0.82 0.07 250);
  --primary-5:  oklch(0.75 0.11 250);
  --primary-6:  oklch(0.67 0.15 250);
  --primary-7:  oklch(0.59 0.19 250);
  --primary-8:  oklch(0.52 0.21 250);
  --primary-9:  oklch(0.48 0.22 250);
  --primary-10: oklch(0.42 0.20 250);

  /* Semantic roles */
  --success: oklch(0.55 0.18 150);
  --warning: oklch(0.60 0.15 80);
  --error:   oklch(0.55 0.20 25);
  --info:    oklch(0.55 0.15 250);
}
```

### 2.3 Semantic токены (роли, не цвета)

```css
:root {
  /* Поверхности */
  --surface-page:     var(--neutral-1);
  --surface-card:     var(--neutral-2);
  --surface-elevated: var(--neutral-3);
  --surface-hover:    var(--neutral-4);

  /* Текст */
  --text-primary:   var(--neutral-12);
  --text-secondary: var(--neutral-11);
  --text-tertiary:  var(--neutral-9);
  --text-muted:     var(--neutral-8);

  /* Границы */
  --border-subtle:  var(--neutral-6);
  --border-default: var(--neutral-7);
  --border-strong:  var(--neutral-8);

  /* Акцент */
  --accent-bg:      var(--primary-9);
  --accent-text:    oklch(1 0 0);
  --accent-hover:   var(--primary-8);
  --accent-subtle:  var(--primary-3);
}
```

**Преимущество:** меняешь тему — меняются только значения переменных, не HTML/CSS.

### 2.4 Light / Dark тема

```css
:root {
  --surface-page: oklch(0.98 0.003 260);
  --text-primary: oklch(0.12 0.01 260);
  /* ... light values */
}

@media (prefers-color-scheme: dark) {
  :root {
    --surface-page: oklch(0.02 0.005 260);
    --text-primary: oklch(0.93 0.008 260);
    /* ... dark values - не инверсия, а отдельная палитра */
  }
}
```

**Правила тёмной темы:**
- Не #000/#fff, а #0A0A0B/#EDEDEF
- Не инверсия светлой — отдельная палитра
- Снизь насыщенность на 20-30% в тёмной теме для комфорта
- Используй слои: surface → raised → overlay (разная яркость, а не монотонный фон)

### 2.5 WCAG-контраст

| Элемент | AA | AAA |
|---------|----|-----|
| Body text (<18pt) | 4.5:1 | 7:1 |
| Large text (≥18pt bold / ≥24pt) | 3:1 | 4.5:1 |
| UI components | 3:1 | — |

**Инструменты проверки:** WebAIM, Stark (Figma), colorfor.ai, axe DevTools.

**Важно:** HSL-лайтнесс врёт. Два цвета с одинаковым L в HSL могут иметь разный perceived контраст. OKLCH даёт предсказуемый контраст.

### 2.6 Составление палитры

**Правило 60/30/10:**
- 60% — нейтральный фон
- 30% — второстепенный нейтральный
- 10% — акцент

**AI-антипаттерны (избегать):**
- Фиолетовый градиент на белом
- Бирюзовый на тёмно-синем
- Неоновый розовый «киберпанк»
- Пять одинаково ярких цветов — глазу некуда смотреть

**Трендовые направления 2026:**
- Приглушённые нейтральные + яркий микро-акцент
- Тёплые earth tones: terracotta, sage, warm beige
- Muted + bold contrast
- Не чистый #FFFFFF, а #FAFAFA, #F5F5F0 (тёплый)
- Не чистый #000000, а #0A0A0B, #111113

### 2.7 color-mix() для динамики

```css
:root {
  --accent-hover:  color-mix(in oklch, var(--accent-bg), black 15%);
  --accent-active: color-mix(in oklch, var(--accent-bg), black 25%);
  --accent-glass:  color-mix(in oklch, var(--accent-bg), transparent 85%);
}
```

Это заменяет десятки хардкодных значений.

---

## 3. Spacing и композиция

### 3.1 Spacing scale (8px baseline)

```css
:root {
  --space-px:  1px;
  --space-0:   0px;
  --space-0.5: 0.125rem; /* 2px */
  --space-1:   0.25rem;  /* 4px */
  --space-2:   0.5rem;   /* 8px */
  --space-3:   0.75rem;  /* 12px */
  --space-4:   1rem;     /* 16px */
  --space-5:   1.25rem;  /* 20px */
  --space-6:   1.5rem;   /* 24px */
  --space-8:   2rem;     /* 32px */
  --space-10:  2.5rem;   /* 40px */
  --space-12:  3rem;     /* 48px */
  --space-14:  3.5rem;   /* 56px */
  --space-16:  4rem;     /* 64px */
  --space-20:  5rem;     /* 80px */
  --space-24:  6rem;     /* 96px */
  --space-28:  7rem;     /* 112px */
  --space-32:  8rem;     /* 128px */
}
```

**Правила:**
- Все отступы кратны 8px (или 4px). 13px, 17px, 22px — запрещены
- Внутри компонента — малые отступы (4-16px)
- Между компонентами — средние (16-32px)
- Между секциями — большие (64-128px)
- Заголовок→подзаголовок 8-12px
- Подзаголовок→тело 16-24px
- Gestalt: близкие элементы = связанные

### 3.2 Сетка

```css
/* Простая центрированная */
.content {
  width: min(100% - var(--space-8), 1200px);
  margin-inline: auto;
}

/* 12-колонная */
.grid-12 {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: var(--space-6);
}

/* Асимметричная */
.grid-asymmetric {
  display: grid;
  grid-template-columns: 1fr 1.5fr;
  gap: var(--space-10);
}

/* Авто-филл (для карточек) */
.grid-auto {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
  gap: var(--space-6);
}
```

**Принципы композиции:**
- 12 колонок — безопасный дефолт, но не единственный вариант
- **Asymmetry > symmetry** для выразительности. Симметрия = спокойствие. Асимметрия = энергия.
- **Z-паттерн** — глаз движется сверху слева → вниз направо (для западного чтения)
- **Diagonal flow** — расставляй якоря (заголовок, CTA, визуал) по этой траектории
- Editorial moves: текст за колонку, крупная цитата на полях, цифра размером с заголовок
- Overlap: карточка наезжает на изображение, цифра выходит за рамку

### 3.3 Атмосфера фона

```css
/* Noise overlay */
.noise {
  position: fixed;
  inset: 0;
  z-index: 10000;
  pointer-events: none;
  opacity: 0.035;
  background-image: url("data:image/svg+xml,...");
  background-size: 200px 200px;
}

/* Grid mesh */
.hero-grid {
  background-image:
    linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px);
  background-size: 64px 64px;
  mask-image: radial-gradient(ellipse 80% 60% at 50% 50%, black, transparent);
}

/* Gradient orb */
.orb {
  width: min(70vw, 700px);
  height: min(70vw, 700px);
  border-radius: 50%;
  background: radial-gradient(circle at 30% 40%, var(--accent-glass), transparent 65%);
  animation: orb-drift 25s ease-in-out infinite;
}

@keyframes orb-drift {
  0%, 100% { transform: translate(0,0) scale(1); }
  33% { transform: translate(30px,-25px) scale(1.05); }
  66% { transform: translate(-20px,15px) scale(0.96); }
}
```

**Философия:** фон — не пустота, а слой атмосферы. Он должен «дышать»: иметь глубину, текстурность, тактильность. Пользователь может не заметить его сознательно, но без него интерфейс кажется плоским и мёртвым.

#### Дополнительные техники

**CSS noise-текстура (без SVG):**
```css
.noise-css {
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.04;
  background-image: repeating-conic-gradient(
      oklch(0 0 0 / 0.03) 0% 25%, transparent 0% 50%
    ) 0 0 / 2px 2px;
  z-index: 10000;
}
```

**Mesh-градиент через несколько наложений:**
```css
.mesh {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 30%, oklch(0.55 0.18 280 / 0.15), transparent 70%),
    radial-gradient(ellipse 60% 70% at 80% 60%, oklch(0.60 0.15 180 / 0.12), transparent 70%),
    radial-gradient(ellipse 50% 50% at 50% 80%, oklch(0.70 0.10 30 / 0.08), transparent 60%);
  mix-blend-mode: overlay;
}
```

**Pattern-оверлей (полоски, точки, клетка):**
```css
.pattern-dots {
  background-image: radial-gradient(oklch(0 0 0 / 0.06) 1px, transparent 1px);
  background-size: 24px 24px;
}

.pattern-stripes {
  background: repeating-linear-gradient(
    45deg,
    transparent,
    transparent 8px,
    oklch(0 0 0 / 0.03) 8px,
    oklch(0 0 0 / 0.03) 9px
  );
}
```

**Living background (микро-движение):**
```css
.living-bg {
  background: var(--surface-page);
  position: relative;
  overflow: hidden;
}

.living-bg::before {
  content: '';
  position: absolute;
  inset: -40%;
  background: conic-gradient(
    from 0deg at 50% 50%,
    transparent 0deg,
    oklch(0.55 0.18 280 / 0.04) 90deg,
    transparent 180deg,
    oklch(0.60 0.12 180 / 0.04) 270deg,
    transparent 360deg
  );
  animation: rotate-bg 40s linear infinite;
  pointer-events: none;
}

@keyframes rotate-bg {
  to { transform: rotate(360deg); }
}
```

**Правила живого фона:**
- Не мешать контенту — opacity ≤ 0.08 для текстур, ≤ 0.15 для градиентов
- Одна техника на секцию, не смешивать шум + mesh + паттерн
- На мобильном отключать сложные фоны или снижать opacity вдвое
- Всегда проверять контраст текста поверх текстуры
- Фон не должен анимироваться, если пользователь включил prefers-reduced-motion

---

## 4. Радиусы и тени

### 4.1 Radius scale

```css
:root {
  --radius-none: 0;
  --radius-xs:   0.125rem;  /* 2px */
  --radius-sm:   0.25rem;   /* 4px */
  --radius-md:   0.5rem;    /* 8px */
  --radius-lg:   0.75rem;   /* 12px */
  --radius-xl:   1rem;      /* 16px */
  --radius-2xl:  1.5rem;    /* 24px */
  --radius-3xl:  2rem;      /* 32px */
  --radius-full: 9999px;    /* pill */
}
```

**Правила:**
- Однотипные компоненты = одинаковый радиус
- Внешний радиус = внутренний радиус + padding карточки
- Squircle: большой border-radius + чуть меньший у вложенных

### 4.2 Shadow scale

```css
:root {
  --shadow-xs:   0 1px 2px oklch(0 0 0 / 0.04);
  --shadow-sm:   0 1px 3px oklch(0 0 0 / 0.06), 0 1px 2px oklch(0 0 0 / 0.04);
  --shadow-md:   0 4px 6px oklch(0 0 0 / 0.06), 0 2px 4px oklch(0 0 0 / 0.04);
  --shadow-lg:   0 10px 15px oklch(0 0 0 / 0.08), 0 4px 6px oklch(0 0 0 / 0.04);
  --shadow-xl:   0 20px 25px oklch(0 0 0 / 0.10), 0 8px 10px oklch(0 0 0 / 0.06);
  --shadow-2xl:  0 25px 50px oklch(0 0 0 / 0.12);
}
```

**Правила:**
- Многослойная тень всегда лучше одной — даёт резкость края + мягкое рассеяние
- Тёплая тень: слегка окрашенная в тон бренда
- В плоском / brutalist стиле — никаких теней
- Каждый уровень = конкретный смысл: карточка / dropdown / popover / modal / hero

---

## 5. Современный CSS-стек 2026

### 5.1 Cascade Layers (@layer)

```css
@layer reset, design-system, components, utilities;

@layer reset {
  *, *::before, *::after { box-sizing: border-box; }
  /* ... reset styles */
}

@layer design-system {
  :root { /* tokens */ }
  .btn { /* base button */ }
}

@layer components {
  .btn-primary { /* specific variant */ }
}

@layer utilities {
  .mt-4 { margin-top: var(--space-4); }
}
```

**Зачем:** specificity wars заканчиваются. Последний слой побеждает независимо от специфичности селектора.

### 5.2 Container Queries

```css
.card-grid {
  container-type: inline-size;
  container-name: card-grid;
}

@container card-grid (max-width: 400px) {
  .card { grid-template-columns: 1fr; }
}

@container card-grid (min-width: 401px) and (max-width: 700px) {
  .card { grid-template-columns: 1fr 1fr; }
}

@container card-grid (min-width: 701px) {
  .card { grid-template-columns: 1fr 1fr 1fr; }
}
```

**Главное:** компонент сам знает, как выглядеть в любом контексте. Не надо ResizeObserver.

### 5.3 :has() — контекстные стили

```css
/* Карточка с изображением */
.card:has(img) { grid-template-rows: auto 1fr; }

/* Форма с ошибкой */
.field:has(.input-error) { border-color: var(--error); }

/* Секция с активной навигацией */
nav:has(a.active) { border-bottom: 2px solid var(--accent); }

/* Родитель, содержащий определённый тип */
.sidebar:has(.cta-button) { padding-bottom: var(--space-8); }
```

### 5.4 Native Nesting

```css
.card {
  background: var(--surface-card);
  border-radius: var(--radius-lg);

  & h3 { font-size: var(--text-xl); }
  & p  { color: var(--text-secondary); }

  &:hover {
    box-shadow: var(--shadow-lg);
    & h3 { color: var(--accent); }
  }
}
```

### 5.5 Subgrid

```css
.card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-6);
}

.card {
  display: grid;
  grid-template-rows: subgrid;  /* наследует строки родителя */
  grid-row: span 3;             /* занимает 3 строки родительской сетки */
  gap: var(--space-2);
}
```

Решает проблему выравнивания карточек по высоте без хаков.

---

## 6. Моушн и микро-взаимодействия

### 6.1 Easing-система

```css
:root {
  --ease-linear:     linear;
  --ease-default:    ease;
  --ease-in:         cubic-bezier(0.4, 0, 1, 1);
  --ease-out:        cubic-bezier(0, 0, 0.2, 1);
  --ease-in-out:     cubic-bezier(0.4, 0, 0.2, 1);

  /* Кастомные — используй их */
  --ease-out-quart:  cubic-bezier(0.25, 1, 0.5, 1);     /* появление */
  --ease-out-expo:   cubic-bezier(0.16, 1, 0.3, 1);     /* мощный вход */
  --ease-in-out-exp: cubic-bezier(0.77, 0, 0.175, 1);   /* между состояниями */
  --ease-drawer:     cubic-bezier(0.32, 0.72, 0, 1);    /* iOS-ящик */
  --ease-spring:     cubic-bezier(0.34, 1.56, 0.64, 1); /* отскок */
  --ease-bounce:     cubic-bezier(0.18, 0.89, 0.32, 1.28); /* пружина */
}
```

**Золотое правило:** никогда `ease-in` для UI. Он медленный в начале — когда пользователь смотрит.

### 6.2 Duration scale

```css
:root {
  --dur-instant:  0ms;
  --dur-faster:   80ms;
  --dur-fast:     120ms;
  --dur-normal:   200ms;
  --dur-slow:     300ms;
  --dur-slower:   400ms;
  --dur-slowest:  500ms;
}
```

| Что | Длительность |
|-----|-------------|
| Press feedback (active) | 80-140ms |
| Tooltip / popover | 120-180ms |
| Dropdown / select | 150-220ms |
| Modal / drawer | 200-400ms |
| Page transition | 250-500ms |
| Маркетинг / повествование | дольше |

**UI-анимации почти никогда не превышают 300ms.** 180ms дропдаун = живо. 400ms = "тормозит".

### 6.3 Микро-паттерны (copy-paste готовые)

**Кнопка с откликом:**
```css
.btn {
  transition:
    transform var(--dur-fast) var(--ease-out-quart),
    box-shadow var(--dur-normal) var(--ease-out),
    background var(--dur-normal) var(--ease-out);
}

.btn:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-lg);
}

.btn:active {
  transform: scale(0.97);
}

@media (prefers-reduced-motion: reduce) {
  .btn { transition: none; }
  .btn:hover { transform: none; }
}
```

**Карточка с подъёмом:**
```css
.card {
  transition:
    transform var(--dur-normal) var(--ease-out-quart),
    box-shadow var(--dur-normal) var(--ease-out);
}

.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-xl);
}
```

**Появление элемента:**
```css
.fade-in {
  opacity: 0;
  transform: translateY(12px) scale(0.98);
  animation: fade-in var(--dur-slow) var(--ease-out-expo) forwards;
}

@keyframes fade-in {
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .fade-in {
    opacity: 1;
    transform: none;
    animation: none;
  }
}
```

**Скелетон с shimmer:**
```css
.skeleton {
  background: var(--surface-hover);
  border-radius: var(--radius-md);
  position: relative;
  overflow: hidden;
}

.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent,
    oklch(1 0 0 / 0.05),
    transparent
  );
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
```

**Счётчик / анимированное число:**
```css
.stat-num {
  font-variant-numeric: tabular-nums;
  transition: all var(--dur-slow) var(--ease-out-expo);
}

/* Для JS-анимации чисел используй Framer Motion или anime.js */
```

**Progress bar:**
```css
.progress {
  height: 4px;
  background: var(--border-subtle);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--accent-bg);
  border-radius: var(--radius-full);
  transition: width var(--dur-normal) var(--ease-out-quart);
}
```

**Loading spinner:**
```css
.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border-subtle);
  border-top-color: var(--accent-bg);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

### 6.4 Transform-origin (важно!)

```css
/* Поповер растёт из точки клика */
.popover {
  transform-origin: var(--origin-x, center) var(--origin-y, top);
}

/* Модалка — из центра (другой слой реальности) */
.modal {
  transform-origin: center;
}

/* Тост — из края */
.toast {
  transform-origin: right center;
}
```

### 6.5 Fidgetability

Лучшие интерфейсы приятно «трогать» без цели:
- hover-эффекты кнопок Vercel
- scroll bounce
- микро-отклик на нажатие
- плавный скролл

Если элемент даёт приятный отклик при бесцельном наведении — пользователь возвращается чаще.

### 6.6 Когда НЕ анимировать

| Частота | Решение |
|---------|---------|
| 100+ раз/день (cmd+k, табы) | Не анимировать вообще |
| Десятки раз (hover, list nav) | Минимум |
| Изредка (модалки, тосты) | Стандартная анимация |
| Редко/первый раз (онбординг) | Можно delight |

---

## 7. Доступность (Accessibility)

### 7.1 WCAG 2.2 AA — обязательный минимум

**POUR:**
- **Perceivable** — информация представлена так, чтобы пользователь мог её воспринимать
- **Operable** — интерфейс работает с клавиатуры, достаточно времени
- **Understandable** — предсказуемое поведение, понятные ошибки
- **Robust** — совместимость с assistive technologies

### 7.2 Семантический HTML

```html
<!-- ❌ Плохо -->
<div class="header">
  <div class="nav">
    <div class="nav-item" onclick="...">Главная</div>
  </div>
</div>

<!-- ✅ Хорошо -->
<header role="banner">
  <nav aria-label="Основная">
    <ul>
      <li><a href="/">Главная</a></li>
    </ul>
  </nav>
</header>

<!-- Структура страницы -->
<header role="banner">...</header>
<nav aria-label="Навигация">...</nav>
<main id="main">
  <article>
    <section aria-labelledby="section-title">
      <h1 id="section-title">...</h1>
    </section>
  </article>
</main>
<footer role="contentinfo">...</footer>
```

### 7.3 Skip link

```html
<a href="#main" class="skip-link">Перейти к содержимому</a>
```

```css
.skip-link {
  position: absolute;
  top: -100%;
  left: var(--space-4);
  padding: var(--space-2) var(--space-4);
  background: var(--accent-bg);
  color: var(--accent-text);
  z-index: 1000;
  border-radius: 0 0 var(--radius-md) var(--radius-md);
}

.skip-link:focus { top: 0; }
```

### 7.4 Focus styles

```css
:focus-visible {
  outline: 2px solid var(--accent-bg);
  outline-offset: 2px;
  border-radius: var(--radius-xs);
}

/* Никогда не убирать focus без замены! */
/* ❌ :focus { outline: none; } — never alone */
```

### 7.5 Цвет не единственный носитель

```css
/* ❌ Только цвет */
.error { color: var(--error); }

/* ✅ Цвет + иконка + текст */
.error {
  color: var(--error);
  &::before { content: '⚠ '; }
}
```

### 7.6 prefers-reduced-motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }

  html { scroll-behavior: auto; }
}
```

### 7.7 ARIA — когда семантики не хватает

```html
<!-- Иконочная кнопка -->
<button aria-label="Закрыть">
  <svg aria-hidden="true">...</svg>
</button>

<!-- Прогресс -->
<div role="progressbar" aria-valuenow="30" aria-valuemin="0" aria-valuemax="100">
  30%
</div>

<!-- Ошибка -->
<input aria-invalid="true" aria-describedby="email-error">
<span id="email-error" role="alert">Введите корректный email</span>
```

---

## 8. Компоненты — полное портфолио

### 8.1 Кнопки

```css
/* Base */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: 0.625rem 1.25rem;
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1;
  border: 1px solid transparent;
  border-radius: var(--radius-lg);
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
  transition:
    transform var(--dur-faster) var(--ease-out-quart),
    background var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}

.btn:hover { transform: translateY(-1px); }
.btn:active { transform: scale(0.97); }

/* Variants */
.btn-primary {
  background: var(--accent-bg);
  color: var(--accent-text);
  box-shadow: var(--shadow-sm);
}
.btn-primary:hover {
  background: var(--accent-hover);
  box-shadow: var(--shadow-md);
}

.btn-secondary {
  background: var(--surface-elevated);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
}
.btn-secondary:hover {
  background: var(--surface-hover);
  border-color: var(--border-strong);
}

.btn-ghost {
  background: transparent;
  color: var(--text-secondary);
  border-color: transparent;
}
.btn-ghost:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
}

.btn-destructive {
  background: var(--error);
  color: white;
}

/* Sizes */
.btn-sm  { padding: 0.375rem 0.75rem; font-size: var(--text-xs); }
.btn-lg  { padding: 0.75rem 1.5rem;   font-size: var(--text-base); }
.btn-xl  { padding: 1rem 2rem;        font-size: var(--text-lg); border-radius: var(--radius-xl); }

/* States */
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

.btn.loading {
  position: relative;
  color: transparent;
  pointer-events: none;
}

.btn.loading::after {
  content: '';
  position: absolute;
  width: 16px;
  height: 16px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
```

### 8.2 Карточки

```css
.card {
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
  transition:
    transform var(--dur-normal) var(--ease-out-quart),
    box-shadow var(--dur-normal) var(--ease-out),
    border-color var(--dur-normal) var(--ease-out);
}

.card-interactive:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--border-accent);
}

.card-glass {
  background: oklch(from var(--surface-card) l c h / 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid oklch(from var(--border-subtle) l c h / 0.3);
}
```

### 8.3 Инпуты

```css
.input {
  width: 100%;
  padding: 0.625rem 0.875rem;
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  color: var(--text-primary);
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  transition:
    border-color var(--dur-fast) var(--ease-out),
    box-shadow var(--dur-fast) var(--ease-out);
  outline: none;
}

.input::placeholder { color: var(--text-tertiary); }

.input:focus {
  border-color: var(--accent-bg);
  box-shadow: 0 0 0 3px var(--accent-subtle);
}

.input--error {
  border-color: var(--error);
}

.input--error:focus {
  box-shadow: 0 0 0 3px color-mix(in oklch, var(--error) 15%, transparent);
}

/* Input group */
.input-group {
  display: flex;
  align-items: center;
  gap: 0;
}

.input-group .input {
  border-radius: var(--radius-lg) 0 0 var(--radius-lg);
}

.input-group .btn {
  border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
  margin-left: -1px;
}
```

### 8.4 Модалки

```css
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: oklch(0 0 0 / 0.5);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: overlay-in var(--dur-normal) var(--ease-out);
}

.modal {
  background: var(--surface-elevated);
  border-radius: var(--radius-xl);
  padding: var(--space-8);
  width: min(90%, 480px);
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: var(--shadow-2xl);
  animation: modal-in var(--dur-slow) var(--ease-out-expo);
}

@keyframes overlay-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes modal-in {
  from {
    opacity: 0;
    transform: scale(0.96) translateY(12px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}
```

### 8.5 Навигация

```css
/* Sticky header */
.header {
  position: sticky;
  top: 0;
  z-index: 100;
  background: oklch(from var(--surface-page) l c h / 0.8);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-subtle);
}

/* CSS-only hamburger */
.nav-toggle { display: none; }

.nav-toggle-label {
  display: none;
  flex-direction: column;
  gap: 5px;
  cursor: pointer;
  padding: 4px;
}

.nav-toggle-label span,
.nav-toggle-label::before,
.nav-toggle-label::after {
  display: block;
  width: 22px;
  height: 2px;
  background: var(--text-primary);
  border-radius: 2px;
  transition: transform var(--dur-normal) var(--ease-out-quart),
              opacity var(--dur-fast);
  content: '';
}

@media (max-width: 768px) {
  .nav-toggle-label { display: flex; }

  .nav-links {
    position: fixed;
    top: 0; right: 0; bottom: 0;
    width: 280px;
    flex-direction: column;
    padding: 6rem 2rem;
    background: var(--surface-elevated);
    border-left: 1px solid var(--border-default);
    transform: translateX(100%);
    transition: transform var(--dur-slow) var(--ease-out-expo);
  }

  .nav-toggle:checked ~ .nav-links {
    transform: translateX(0);
  }

  .nav-toggle:checked ~ .nav-toggle-label span { opacity: 0; }
  .nav-toggle:checked ~ .nav-toggle-label::before {
    transform: translateY(7px) rotate(45deg);
  }
  .nav-toggle:checked ~ .nav-toggle-label::after {
    transform: translateY(-7px) rotate(-45deg);
  }
}
```

### 8.6 Таблицы

```css
.table {
  width: 100%;
  border-collapse: collapse;
}

.table th {
  text-align: left;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-subtle);
}

.table td {
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  border-bottom: 1px solid var(--border-subtle);
}

.table td.num {
  font-variant-numeric: tabular-nums;
  text-align: right;
  font-family: var(--font-mono);
}

.table tr:hover td {
  background: var(--surface-hover);
}
```

---

## 9. Контент и структура страницы

### 9.1 Что делает Hero работающим

Первый экран — 80% решения "остаться или уйти".

```html
<section class="hero">
  <span class="hero-label">AI-ассистент для разработчиков</span>
  <h1>12 секунд — и вы знаете <br>о своём коде <span class="accent-word">всё</span></h1>
  <p>Atlas анализирует 100 000 строк кода, пишет документацию и проверяет pull request'ы быстрее, чем вы выпьете кофе.</p>

  <div class="hero-actions">
    <a href="#" class="btn btn-primary btn-lg">Начать бесплатно</a>
    <a href="#" class="btn btn-ghost btn-lg">Посмотреть демо →</a>
  </div>

  <div class="hero-stats">
    <div class="stat">
      <span class="stat-num">10 000+</span>
      <span class="stat-label">разработчиков используют</span>
    </div>
    <div class="stat">
      <span class="stat-num">5 млн</span>
      <span class="stat-label">строк проанализировано</span>
    </div>
    <div class="stat">
      <span class="stat-num">97%</span>
      <span class="stat-label">точности код-ревью</span>
    </div>
  </div>
</section>
```

**Ключевые элементы:**
- Конкретное обещание с цифрами, не лозунг
- Один primary CTA (второй — ghost)
- Доказательство: stats, логотипы, скриншот
- Заголовок отвечает на «зачем мне это», не повторяет название продукта

### Принцип «Открывай характерно»

Hero должен якорить пользователя в **реальности**, а не в обещаниях. Не «AI-powered platform», а конкретный факт.

**Что значит «открывать характерно»:**
- Покажи цену, цифру, расписание, афишу — то, что можно сразу понять и запомнить
- Начни с **узнаваемого якоря**: «12 секунд», «$0 до первой нагрузки», «5 млн строк проанализировано»
- Не заставляй пользователя думать: «а что это даёт?» — ответь в первой строке
- Если продукт можно показать (код-блок, скриншот, фото) — покажи, не описывай

**Примеры:**
| Было (абстрактно) | Стало (конкретно) |
|-------------------|-------------------|
| AI-powered platform to scale your business | Развёртывай Postgres за 90 секунд. $0 до первой нагрузки |
| The future of work | Сократи время код-ревью с 4 часов до 12 минут |
| Next-gen solution | 10 000 разработчиков уже используют |

**Контрольный вопрос:** Если убрать название бренда из hero, останется ли понятно, о чём продукт? Если нет — переделывай.

### 9.2 Структура секций (антишаблоны)

**AI-slop detector — если есть 3+ из этого списка, переделывай:**

- [ ] 3 карточки features в ряд с иконкой и двумя строками текста
- [ ] How it works из 3 банальных шагов со стрелочками
- [ ] Testimonials без фото и должности
- [ ] "Ready to get started?" CTA в конце = копия hero
- [ ] FAQ из выдуманных вопросов
- [ ] "Our values" с иконками Innovation/Trust/Excellence
- [ ] Trusted by с серыми логотипами
- [ ] 4 stats в ряд
- [ ] Слова: revolutionary, next-gen, seamless, cutting-edge, empowering
- [ ] Все секции одинаковой высоты

**Что делать вместо:**
- **Features:** покажи в действии (код-блок, скриншот, видео), а не подпиши иконками
- **How it works:** расскажи историю с реальным примером, не «зарегистрируйся → настрой → получи»
- **Testimonials:** один развёрнутый отзыв с конкретным результатом
- **CTA в конце:** заверши чем-то осмысленным, не повторением hero

### 9.3 Hero без картинки (для dev-тулов)

```html
<section class="hero">
  <!-- Атмосфера -->
  <div class="hero-grid" aria-hidden="true"></div>
  <div class="hero-orb" aria-hidden="true"></div>

  <div class="container">
    <span class="hero-label">...</span>
    <h1>...</h1>
    <p>...</p>

    <!-- Код-блок прямо в hero (для dev-продуктов) -->
    <div class="code-block">
      <code>
        <span class="code-line"><span class="code-num">41</span><span class="code-keyword">async function</span> <span class="code-func">analyze</span>() {</span>
        <span class="code-line code-added">+  result = <span class="code-keyword">await</span> atlas.scan(repo)</span>
        <span class="code-line code-removed">-  <span class="code-comment">// TODO: do it manually</span></span>
        <span class="code-line">}</span>
      </code>
    </div>

    <div class="hero-stats">...</div>
  </div>
</section>
```

Код-блок + diff (added/removed) для dev-продукта — это конкретика, которую AI не генерирует.

### 9.4 Тон голоса (microcopy)

```html
<!-- ❌ Плохо -->
<button>Save</button>
<p>No data</p>
<p>Error 500</p>

<!-- ✅ Хорошо -->
<button>Save changes</button>
<p>No invoices yet — create your first one</p>
<p>That's on us. Try again in a minute.</p>
```

**Правила:**
- Конкретика бьёт абстракцию. «Faster, smarter» — мусор. «Ответ за 180ms» — содержание.
- Глаголы сильнее существительных: «оптимизирует» > «платформа для оптимизации»
- Без точек в кнопках
- «Не нужна кредитная карта» > «Free trial»
- Признай продукт от первого лица: «Мы построили это, потому что…»

---

## 10. Responsive дизайн

### 10.1 Брейкпоинты

```css
/* Mobile-first — min-width */
/* 320px — base (mobile-first) */
/* 480px — large phone */
/* 768px — tablet */
/* 1024px — landscape tablet / small desktop */
/* 1280px — desktop */
/* 1440px — wide desktop */
/* 1920px — huge */

@media (min-width: 768px) { /* tablet+ */ }
@media (min-width: 1024px) { /* desktop+ */ }
@media (min-width: 1280px) { /* wide */ }
```

### 10.2 Responsive layout patterns

```css
/* Stack on mobile, row on desktop */
.responsive-layout {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

@media (min-width: 768px) {
  .responsive-layout {
    flex-direction: row;
    align-items: center;
  }
}

/* Auto-grid (карточки) */
.card-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-6);
}

@media (min-width: 480px) {
  .card-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (min-width: 1024px) {
  .card-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

/* Container queries вместо media queries для компонентов */
.card-container {
  container-type: inline-size;
}

@container (min-width: 400px) {
  .card { flex-direction: row; }
}
```

### 10.3 Mobile specifics

```css
/* Touch targets */
.btn, .nav-link, .card-interactive {
  min-height: 44px;
  min-width: 44px;
}

/* Forms on mobile */
@media (max-width: 480px) {
  input, select, textarea {
    font-size: 16px;  /* prevents iOS zoom on focus */
  }
}

/* Bottom sheet instead of modal */
@media (max-width: 480px) {
  .modal {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    max-height: 90vh;
    animation: sheet-in var(--dur-slow) var(--ease-out-expo);
  }

  @keyframes sheet-in {
    from { transform: translateY(100%); }
    to   { transform: translateY(0); }
  }
}
```

---

## 11. Детали, которые читаются как «дорого»

Сумма этих мелочей отделяет интерфейс на $50 от интерфейса на $5M:

```css
/* 1. Тонкая внутренняя обводка на тёмных карточках */
.card-glass {
  box-shadow: inset 0 0 0 1px oklch(1 0 0 / 0.06);
}

/* 2. Выделение текста под бренд */
::selection {
  background: var(--accent-bg);
  color: var(--accent-text);
}

/* 3. Кастомный скроллбар */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--border-default);
  border-radius: 3px;
}

/* 4. Hover не мгновенный */
* { transition-duration: 0.2s; }

/* 5. Фокус на всём */
:focus-visible { outline: 2px solid var(--accent-bg); outline-offset: 2px; }

/* 6. Сглаживание */
html { -webkit-font-smoothing: antialiased; }

/* 7. Числа — моноширинные */
.num { font-variant-numeric: tabular-nums; }

/* 8. Border не #000, а rgba/oklch */
.border { border: 1px solid var(--border-subtle); }

/* 9. Кастомный курсор для draggable */
.draggable { cursor: grab; }
.draggable:active { cursor: grabbing; }

/* 10. text-wrap: balance для заголовков */
h1, h2, h3 { text-wrap: balance; }

/* 11. Проверка орфографии: кавычки «», тире —, неразрывные пробелы */
```

- Оптический цент: иконка play кажется левее, если геометрически по центру — двигай на 1px
- Loader: быстрый спиннер ощущается быстрее, даже если время то же
- Skeleton > spinner для контента с известной структурой
- Empty states: не «нет данных», а иллюстрация + следующее действие
- Favicon: анимированный для real-time продуктов

---

## 12. Полный чеклист перед сдачей

### 12.1 Design audit

- [ ] Token inventory — все значения через переменные? Нет магических чисел?
- [ ] Component inventory — одинаковые компоненты выглядят одинаково?
- [ ] Semantic HTML — header, nav, main, section, article, footer?
- [ ] Keyboard — Tab, Shift+Tab, Enter, Space, Escape работают?
- [ ] Focus — focus-visible на всём?
- [ ] Contrast — body 4.5:1, large 3:1?
- [ ] Color — цвет не единственный носитель информации?
- [ ] Alt — у всех img есть alt (или alt="" для декоративных)?
- [ ] Reduced motion — prefers-reduced-motion обработан?
- [ ] Color scheme — light + dark одинаково строги?
- [ ] Responsive — 320/480/768/1024/1280, нет горизонтального скролла?
- [ ] Touch — target ≥44px на мобильном?
- [ ] Typography — modular scale, measure 60-75, fluid clamp()?
- [ ] Motion — все анимации оправданы? Easing кастомный?
- [ ] Buttons — :hover, :active, :focus-visible, :disabled, loading?
- [ ] Cards — hover, empty state, skeleton?
- [ ] Forms — labels, errors, disabled, focus?
- [ ] Navigation — active state, mobile hamburger, sticky header?
- [ ] Photos — реальные, не placeholder? (через image_search)
- [ ] Microcopy — без точек в кнопках, конкретно, понятно?

### 12.2 AI-slop detector

Если 3+ «да» — переделывай:
- [ ] 3 карточки features с иконкой и двумя строками
- [ ] How it works из 3 шагов
- [ ] Testimonials без фото/должности
- [ ] Hero-заголовок можно поставить конкуренту
- [ ] Stock-иллюстрация вместо продукта
- [ ] «Ready to get started?» CTA = копия hero
- [ ] Our values с иконками
- [ ] Слова: revolutionary, next-gen, seamless, cutting-edge
- [ ] Все секции одинаковой высоты

---

## 13. Аудит существующего проекта

Когда задача — отполировать существующий код, работай по протоколу:

### Phase 1 — Discovery
1. Прочти все файлы, построй карту UI-поверхности
2. Token inventory — выпиши все цвета, шрифты, размеры, радиусы, тени
3. Component inventory — перечисли все типы, найди дубликаты
4. Mental screenshot review — пройди каждый экран
5. Audit report по severity: 🔴 Critical / 🟠 Major / 🟡 Minor / 🟢 Enhancement

### Phase 2 — Foundation
Порядок: цвет → типографика → spacing → радиусы → тени → motion

### Phase 3 — Component refactoring
Каждый компонент проверь по всем состояниям.

### Phase 4 — Layout + responsive
max-width, брейкпоинты, touch targets, нет горизонтального скролла.

### Phase 5 — Polish
`:active scale(0.97)`, smooth-hover, focus-visible, skeleton, `::selection`, lazy load, text-overflow.

### Phase 6 — Accessibility
Keyboard, contrast, alt, ARIA, heading hierarchy, semantic HTML.

### Формат аудита

| Before | After | Why |
|--------|-------|-----|
| `color: #5f5f63` | `var(--text-secondary)` | Хардкод → токен, работает в обеих темах |
| `ease-in 300ms` | `ease-out 200ms var(--ease-out-quart)` | Вход с ease-in тормозит в начале |
| `scale(0)` | `scale(0.96) opacity: 0` | В реальности ничто не изникает из ничего |
| `padding: 13px` | `padding: var(--space-3)` (12px) | Магическое число → токен |

---

## 14. Реальные изображения

Не оставляй серые плейсхолдеры. Используй `image_search` с `download: true`.

**Процесс:**
1. Загрузи скилл `web` (через `skill("web")`)
2. Найди кандидатов: `image_search({"query": "moody dark office workspace", "max_results": 8})`
3. Скачай: `image_search({"query": "moody dark office workspace", "download": true, "download_dir": "assets/images"})`
4. Подставь реальные пути в `src`
5. Добавь `alt`, `loading="lazy"`, `aspect-ratio`

---

## 15. Правила работы (сводка)

1. **Сначала направление, потом код** — назови эстетик-якорь до первого тега
2. **Никаких generic-комбинаций** — Inter + фиолетовый = redo
3. **Характерные шрифты** — display с характером, body читаемый. Не Inter «по умолчанию»
4. **Системность > индивидуальный блеск** — сначала токены, потом компоненты
5. **OKLCH > HEX/HSL** — цвета в современном пространстве
6. **Container Queries** — компонент управляет собой
7. **Каждая анимация оправдана** — не знаешь зачем = убери
8. **Accessibility не отдельный шаг** — семантика и контраст с первой строки
9. **Light + dark** — оба строги, не инверсия
10. **Избегай AI-шаблонов** — проверь по чеклисту 12.2
11. **Match complexity to vision** — maximalist требует деталей, minimalist точности. Середина = провал
12. **Покажи характер** — не сдерживайся ради безопасности. Безопасно = AI-slop
13. **Наполняй картинками** — реальные, скачанные через image_search, не placeholder
14. **Детали решают** — сумма микро-улучшений даёт премиум-ощущение
15. **Проверяй на мобильном** — большинство проблем видно только там

---

## 16. Быстрые CSS-рецепты

### Герой с градиентным текстом
```css
.gradient-text {
  background: linear-gradient(135deg, var(--text-primary) 40%, var(--accent) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
```

### Разделитель с градиентом
```css
.divider {
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--border-default), transparent);
  border: none;
}
```

### Акцентная линия
```css
.accent-line {
  width: 24px;
  height: 3px;
  background: var(--accent-bg);
  border-radius: var(--radius-full);
}
```

### Стеклянная карточка
```css
.glass {
  background: oklch(from var(--surface-card) l c h / 0.5);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid oklch(1 0 0 / 0.08);
}
```

### Счётчик / badge
```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.125rem 0.5rem;
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-full);
  background: var(--accent-subtle);
  color: var(--accent-bg);
}
```

### Кнопка "pill"
```css
.btn-pill {
  border-radius: var(--radius-full);
  padding: 0.5rem 1.25rem;
}
```

### Ссылка с подчёркиванием при hover
```css
.link-underline {
  color: var(--text-secondary);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color var(--dur-fast) var(--ease-out);
}

.link-underline:hover {
  color: var(--text-primary);
  border-color: var(--text-primary);
}
```

### Аватар / инициалы
```css
.avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-full);
  background: var(--accent-subtle);
  color: var(--accent-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-weight: 600;
}
```

### Tooltip (CSS-only)
```css
.tooltip {
  position: relative;
}

.tooltip::after {
  content: attr(data-tooltip);
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%) translateY(2px);
  padding: 4px 8px;
  font-size: var(--text-xs);
  white-space: nowrap;
  background: var(--text-primary);
  color: var(--surface-page);
  border-radius: var(--radius-sm);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--dur-fast) var(--ease-out),
              transform var(--dur-fast) var(--ease-out);
}

.tooltip:hover::after {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
```

---

## 17. Формы — полный набор состояний

### 17.1 Структура формы

```html
<form class="form" novalidate>
  <div class="form-field">
    <label class="form-label" for="email">Email</label>
    <div class="form-input-wrap">
      <input type="email" id="email" class="input" placeholder="you@example.com"
             required autocomplete="email"
             aria-describedby="email-hint">
      <span class="input-icon" aria-hidden="true">✉</span>
    </div>
    <p class="form-hint" id="email-hint">Мы никогда не передадим ваш email третьим лицам</p>
    <p class="form-error" id="email-error" role="alert">
      <span aria-hidden="true">⚠</span> Введите корректный email
    </p>
  </div>

  <div class="form-field">
    <label class="form-label" for="password">Пароль</label>
    <div class="form-input-wrap">
      <input type="password" id="password" class="input"
             required minlength="8" autocomplete="new-password">
      <button type="button" class="input-suffix" aria-label="Показать пароль"
              onclick="togglePassword()">
        <svg>...</svg>
      </button>
    </div>
    <div class="form-progress">
      <div class="progress-bar" style="width: 60%"></div>
    </div>
  </div>

  <div class="form-field">
    <label class="form-checkbox">
      <input type="checkbox">
      <span class="checkbox-custom"></span>
      Я соглашаюсь с <a href="#">условиями обработки</a>
    </label>
  </div>

  <button type="submit" class="btn btn-primary btn-lg form-submit">
    Создать аккаунт
  </button>
</form>
```

### 17.2 Стили формы

```css
.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 400px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.form-label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.form-hint {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  margin-top: var(--space-0-5);
}

.form-error {
  font-size: var(--text-xs);
  color: var(--error);
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-top: var(--space-0-5);
  display: none;
}

.form-field--error .form-error { display: flex; }
.form-field--error .input { border-color: var(--error); }

.form-input-wrap {
  position: relative;
}

.input-icon {
  position: absolute;
  left: var(--space-3);
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-tertiary);
  pointer-events: none;
}

.form-input-wrap .input {
  padding-left: var(--space-10);  /* место для иконки */
}

.input-suffix {
  position: absolute;
  right: var(--space-2);
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: var(--space-1);
  border-radius: var(--radius-sm);
}

.input-suffix:hover { color: var(--text-secondary); }

/* Checkbox */
.form-checkbox {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  cursor: pointer;
}

.form-checkbox input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.checkbox-custom {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 2px solid var(--border-default);
  border-radius: var(--radius-xs);
  background: var(--surface-card);
  transition: all var(--dur-fast) var(--ease-out);
  flex-shrink: 0;
}

.form-checkbox input:checked + .checkbox-custom {
  background: var(--accent-bg);
  border-color: var(--accent-bg);
}

.form-checkbox input:checked + .checkbox-custom::after {
  content: '';
  width: 4px;
  height: 8px;
  border: solid var(--accent-text);
  border-width: 0 2px 2px 0;
  transform: rotate(45deg) translateY(-1px);
}

.form-checkbox input:focus-visible + .checkbox-custom {
  outline: 2px solid var(--accent-bg);
  outline-offset: 2px;
}
```

### 17.3 Форма с мульти-шагом

```html
<div class="form-stepper">
  <div class="step-indicators">
    <div class="step-indicator active" data-step="1">
      <span class="step-num">1</span>
      <span class="step-label">Аккаунт</span>
    </div>
    <div class="step-connector"></div>
    <div class="step-indicator" data-step="2">
      <span class="step-num">2</span>
      <span class="step-label">Профиль</span>
    </div>
    <div class="step-connector"></div>
    <div class="step-indicator" data-step="3">
      <span class="step-num">3</span>
      <span class="step-label">Готово</span>
    </div>
  </div>

  <div class="step-panels">
    <div class="step-panel active" data-panel="1">...</div>
    <div class="step-panel" data-panel="2">...</div>
    <div class="step-panel" data-panel="3">...</div>
  </div>
</div>
```

```css
.step-indicators {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  margin-bottom: var(--space-8);
}

.step-indicator {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  opacity: 0.4;
  transition: opacity var(--dur-normal) var(--ease-out);
}

.step-indicator.active { opacity: 1; }
.step-indicator.completed .step-num {
  background: var(--accent-bg);
  color: var(--accent-text);
}

.step-num {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background: var(--surface-elevated);
  border: 1px solid var(--border-default);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-secondary);
}

.step-connector {
  width: 60px;
  height: 1px;
  background: var(--border-default);
  margin: 0 var(--space-3);
}

.step-label {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.step-panel { display: none; }
.step-panel.active { display: block; }
```

### 17.4 Select / Dropdown

```css
.select-wrap {
  position: relative;
}

.select-wrap::after {
  content: '';
  position: absolute;
  right: var(--space-3);
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 10px;
  border: 2px solid var(--text-tertiary);
  border-width: 0 2px 2px 0;
  transform: translateY(-70%) rotate(45deg);
  pointer-events: none;
}

.select {
  width: 100%;
  padding: 0.625rem 2.5rem 0.625rem 0.875rem;
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  color: var(--text-primary);
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  appearance: none;
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out);
}

.select:focus {
  border-color: var(--accent-bg);
  box-shadow: 0 0 0 3px var(--accent-subtle);
  outline: none;
}
```

### 17.5 Тоггл / Switch

```css
.toggle {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  cursor: pointer;
}

.toggle input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-track {
  width: 44px;
  height: 24px;
  background: var(--border-default);
  border-radius: var(--radius-full);
  transition: background var(--dur-fast) var(--ease-out);
  position: relative;
  flex-shrink: 0;
}

.toggle-track::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 20px;
  height: 20px;
  background: white;
  border-radius: var(--radius-full);
  transition: transform var(--dur-fast) var(--ease-out-quart);
  box-shadow: var(--shadow-sm);
}

.toggle input:checked + .toggle-track {
  background: var(--accent-bg);
}

.toggle input:checked + .toggle-track::after {
  transform: translateX(20px);
}

.toggle input:focus-visible + .toggle-track {
  outline: 2px solid var(--accent-bg);
  outline-offset: 2px;
}
```

---

## 18. Дата-визуализация / Дашборды

### 18.1 Статистические карточки

```html
<div class="stat-card">
  <div class="stat-header">
    <span class="stat-title">Выручка</span>
    <span class="stat-change stat-change--up">+12.5%</span>
  </div>
  <div class="stat-value">$45,289</div>
  <div class="stat-footer">за последние 30 дней</div>
</div>
```

```css
.stat-card {
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
}

.stat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}

.stat-title {
  font-size: var(--text-sm);
  color: var(--text-tertiary);
  font-weight: 500;
}

.stat-change {
  font-size: var(--text-xs);
  font-weight: 600;
  padding: 0.125rem 0.5rem;
  border-radius: var(--radius-full);
}

.stat-change--up {
  background: color-mix(in oklch, var(--success) 12%, transparent);
  color: var(--success);
}

.stat-change--down {
  background: color-mix(in oklch, var(--error) 12%, transparent);
  color: var(--error);
}

.stat-value {
  font-size: var(--text-3xl);
  font-weight: 700;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  margin-bottom: var(--space-1);
}

.stat-footer {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}
```

### 18.2 Прогресс-бар (множественный)

```css
.progress-stacked {
  display: flex;
  height: 8px;
  border-radius: var(--radius-full);
  overflow: hidden;
  gap: 2px;
}

.progress-stacked .segment {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width var(--dur-normal) var(--ease-out-quart);
}

.progress-stacked .segment--primary { background: var(--accent-bg); }
.progress-stacked .segment--success { background: var(--success); }
.progress-stacked .segment--warning { background: var(--warning); }
.progress-stacked .segment--error   { background: var(--error); }
```

### 18.3 Легенда для графиков

```css
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  list-style: none;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}
```

---

## 19. Анимации готовые (copy-paste)

### 19.1 Stagger — поочередное появление

```html
<div class="stagger-container">
  <div class="stagger-item" style="--i: 0">...</div>
  <div class="stagger-item" style="--i: 1">...</div>
  <div class="stagger-item" style="--i: 2">...</div>
</div>
```

```css
.stagger-container {
  & > * {
    opacity: 0;
    transform: translateY(16px);
    animation: stagger-in 0.5s var(--ease-out-expo) forwards;
    animation-delay: calc(var(--i, 0) * 0.1s);
  }
}

@keyframes stagger-in {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

### 19.2 Счётчик / Число с запятой

```css
.counter-value {
  font-variant-numeric: tabular-nums;
  transition: all var(--dur-slow) var(--ease-out-expo);
}
```

При обновлении через JS — анимируй через Framer Motion `useSpring` или `requestAnimationFrame`.

### 19.3 Появление секции при скролле

```html
<section class="reveal">
  <div class="reveal-content">
    <h2>...</h2>
    <p>...</p>
  </div>
</section>
```

```css
.reveal-content {
  opacity: 0;
  transform: translateY(24px);
  transition: opacity 0.6s var(--ease-out-expo),
              transform 0.6s var(--ease-out-expo);
}

.reveal.is-visible .reveal-content {
  opacity: 1;
  transform: translateY(0);
}
```

```js
// Intersection Observer
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('is-visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
```

### 19.4 Page load animation

```css
.page-enter {
  animation: page-in 0.4s var(--ease-out-expo) both;
}

@keyframes page-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

### 19.5 Pulse / внимание

```css
@keyframes pulse-soft {
  0%, 100% { box-shadow: 0 0 0 0 var(--accent-subtle); }
  50% { box-shadow: 0 0 0 8px transparent; }
}

.pulse {
  animation: pulse-soft 2s var(--ease-in-out) infinite;
}
```

### 19.6 Notification / Toast

```css
.toast {
  position: fixed;
  bottom: var(--space-6);
  right: var(--space-6);
  padding: var(--space-4) var(--space-6);
  background: var(--surface-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  animation:
    toast-in var(--dur-slow) var(--ease-out-expo) forwards;
  z-index: 2000;
}

.toast-exit {
  animation: toast-out var(--dur-normal) var(--ease-in) forwards;
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes toast-out {
  to {
    opacity: 0;
    transform: translateY(-8px) scale(0.96);
  }
}
```

### 19.7 Notification dot / badge

```css
.notification-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--error);
  position: relative;
}

.notification-dot--live::after {
  content: '';
  position: absolute;
  inset: -2px;
  border-radius: inherit;
  background: var(--error);
  animation: ping 1.5s var(--ease-in-out) infinite;
}

@keyframes ping {
  75%, 100% {
    transform: scale(2);
    opacity: 0;
  }
}
```

---

## 20. E-commerce паттерны

### 20.1 Карточка товара

```css
.product-card {
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  overflow: hidden;
  transition: all var(--dur-normal) var(--ease-out-quart);
}

.product-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--border-default);
}

.product-image {
  aspect-ratio: 4/3;
  background: var(--surface-hover);
  position: relative;
  overflow: hidden;
}

.product-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform var(--dur-slow) var(--ease-out);
}

.product-card:hover .product-image img {
  transform: scale(1.05);
}

.product-info {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.product-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.product-price {
  font-size: var(--text-base);
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.product-price--old {
  font-size: var(--text-sm);
  color: var(--text-tertiary);
  text-decoration: line-through;
}

.product-rating {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--text-tertiary);
}
```

### 20.2 Pricing cards

```html
<div class="pricing-grid">
  <div class="pricing-card">
    <h3>Starter</h3>
    <div class="pricing-amount">
      <span class="price">$19</span>
      <span class="period">/мес</span>
    </div>
    <ul class="pricing-features">
      <li class="pricing-feature">5 проектов</li>
      <li class="pricing-feature">10 000 запросов</li>
      <li class="pricing-feature">Базовая поддержка</li>
    </ul>
    <a href="#" class="btn btn-secondary btn-lg">Начать</a>
  </div>

  <div class="pricing-card pricing-card--featured">
    <span class="pricing-badge">Популярное</span>
    <h3>Pro</h3>
    <div class="pricing-amount">
      <span class="price">$49</span>
      <span class="period">/мес</span>
    </div>
    <ul class="pricing-features">
      <li class="pricing-feature featured">Безлимит проектов</li>
      <li class="pricing-feature featured">100 000 запросов</li>
      <li class="pricing-feature featured">Приоритетная поддержка</li>
    </ul>
    <a href="#" class="btn btn-primary btn-lg">Начать</a>
  </div>
</div>
```

```css
.pricing-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  gap: var(--space-6);
  align-items: start;
}

.pricing-card {
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-xl);
  padding: var(--space-8);
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  position: relative;
}

.pricing-card--featured {
  border-color: var(--accent-bg);
  box-shadow: 0 0 0 1px var(--accent-bg);
  transform: scale(1.03);
}

.pricing-badge {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  padding: 0.25rem 0.75rem;
  font-size: var(--text-xs);
  font-weight: 600;
  background: var(--accent-bg);
  color: var(--accent-text);
  border-radius: var(--radius-full);
}

.pricing-card h3 {
  font-size: var(--text-xl);
  font-weight: 700;
}

.pricing-amount {
  display: flex;
  align-items: baseline;
  gap: var(--space-1);
}

.price {
  font-size: var(--text-5xl);
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.period {
  font-size: var(--text-sm);
  color: var(--text-tertiary);
}

.pricing-features {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.pricing-feature {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.pricing-feature::before {
  content: '✓';
  color: var(--success);
  font-weight: 700;
}

.pricing-feature.featured::before {
  color: var(--accent-bg);
}
```

---

## 21. Типовые секции и альтернативы шаблонам

### 21.1 Вместо «3 карточек features» — покажи продукт в действии

```html
<section class="feature-demo">
  <div class="feature-content">
    <span class="section-label">Глубокий анализ кода</span>
    <h2>Находит баги до того, как они станут проблемой</h2>
    <p>Atlas сканирует всю кодовую базу, выявляет узкие места, уязвимости и нарушения стандартов. Результат — за секунды.</p>
    <div class="feature-stats">
      <div class="feature-stat">
        <span class="stat-num">5 млн</span>
        <span class="stat-label">строк проанализировано</span>
      </div>
      <div class="feature-stat">
        <span class="stat-num">97%</span>
        <span class="stat-label">точности</span>
      </div>
    </div>
  </div>
  <div class="feature-visual">
    <!-- Код-блок с diff — для dev-продукта -->
    <div class="code-block">
      <span class="code-line code-added">+ const result = await atlas.analyze(repo);</span>
      <span class="code-line">- // FIXME: we need to check this later</span>
      <span class="code-line">+ return result.report();</span>
    </div>
  </div>
</section>
```

### 21.2 Вместо «How it works 3 шага» — убери банальности

Если процесс действительно «Зарегистрируйся → Настрой → Пользуйся» — не показывай его. Это трата места. Вместо этого:

- Покажи **скриншот реального процесса** с подписями
- Расскажи **историю одного клиента**: было → стало
- Покажи **архитектуру** продукта (если технический)
- Сделай **сравнительную таблицу** «было vs стало»

### 21.3 Вместо «Testimonials» — кейс с цифрами

```html
<section class="case-study">
  <blockquote>
    <p>«Мы сократили время код-ревью с 4 часов до 12 минут. Atlas находит проблемы, которые мы пропускали годами.»</p>
    <footer>
      <cite>— Артём Петров, Tech Lead в CloudScale</cite>
    </footer>
  </blockquote>
  <div class="case-results">
    <div class="case-stat">
      <span class="stat-num">95%</span>
      <span class="stat-label">покрытие тестами</span>
    </div>
    <div class="case-stat">
      <span class="stat-num">4 ч → 12 мин</span>
      <span class="stat-label">время ревью</span>
    </div>
    <div class="case-stat">
      <span class="stat-num">2×</span>
      <span class="stat-label">скорость релизов</span>
    </div>
  </div>
</section>
```

### 21.4 Hero с убеждением — не «AI-powered platform», а конкретика

| ❌ Лозунг | ✅ Конкретика |
|-----------|--------------|
| AI-powered platform to scale your business | Развёртывай Postgres за 90 секунд. $0 до первой нагрузки |
| The future of work | Сократи время код-ревью с 4 часов до 12 минут |
| Next-gen solution | 10 000 разработчиков уже используют |
| Revolutionary platform | 5 млн строк проанализировано, 97% точности |

---

## 22. Конкретные сценарии по типу продукта

### 22.1 SaaS-лендинг
- Hero: конкретное обещание + stats или логотипы
- Feature: 1 крупная (показ продукта) + 2 дополнительных (текст)
- Social proof: кейс с цифрами
- Pricing: 2-3 тарифа
- CTA: бесплатный триал без карты
- **Шрифты:** утварный display + нейтральный body. Цвет: синий/B2B-безопасный

### 22.2 Dev Tool
- Hero: код-блок с diff (+++ / ---) прямо во viewport
- Feature: живой playground / REPL
- Docs reference: API, changelog
- **Шрифты:** моноширинный для кода, характерный sans для заголовков. Тёмная тема по умолчанию.

### 22.3 E-commerce
- Hero: крупное product-фото + цена + CTA
- Grid: auto-fill карточки с изображениями
- Фильтры: sidebar или top bar с facets
- **Цвет:** нейтральный фон, акцент на CTA. Фото — главный элемент.

### 22.4 Портфолио / Личный сайт
- Hero: крупная типографика, имя + роль
- Work: masonry или staggered grid
- About: текст от первого лица
- **Шрифты:** editorial serif, много воздуха, настроение

### 22.5 Дашборд / Admin
- Sidebar навигация
- Stats grid: 2-4 карточки с цифрами
- Таблицы с сортировкой
- **Цвет:** плотный, нейтральный, минимум акцента. Шрифт: sans с низкой x-height для компактности.

---

## 23. SEO и meta для лендингов

```html
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Atlas — AI-ассистент разработчика | Анализ кода и авто-документация</title>
  <meta name="description" content="Atlas анализирует код, пишет документацию и проверяет PR. 10 000+ разработчиков используют. Начните бесплатно.">
  <meta name="robots" content="index, follow">

  <!-- Open Graph -->
  <meta property="og:title" content="Atlas — AI-ассистент разработчика">
  <meta property="og:description" content="Анализ кода, документация и код-ревью на AI. 12 секунд — и вы знаете о своём коде всё.">
  <meta property="og:image" content="https://atlas.dev/og.png">
  <meta property="og:type" content="website">

  <!-- Twitter Card -->
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="Atlas — AI для разработчиков">
  <meta name="twitter:description" content="Код, который говорит сам за себя.">

  <!-- Favicon -->
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">

  <!-- Preload LCP font -->
  <link rel="preload" as="font" href="/fonts/Geist-Variable.woff2" crossorigin>

  <!-- JSON-LD Structured Data -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    "name": "Atlas",
    "applicationCategory": "DeveloperApplication",
    "operatingSystem": "Web",
    "description": "AI-ассистент для анализа кода, документации и код-ревью"
  }
  </script>
</head>
```

---

## 24. Производительность (Core Web Vitals)

```css
/* Предотвращение CLS от шрифтов */
@font-face {
  font-family: 'Geist';
  src: url('/fonts/Geist-Variable.woff2');
  font-display: swap;
  size-adjust: 95%;     /* ← ключ: соответствие fallback-шрифту */
  ascent-override: 90%;
  descent-override: 22%;
}

/* Lazy loading изображений */
img { loading="lazy"; }

/* Placeholder для изображений */
.image-wrapper {
  position: relative;
  aspect-ratio: 16/9;
  background: var(--surface-hover);
  overflow: hidden;
}

/* Content-visibility для секций ниже fold */
.section-below {
  content-visibility: auto;
  contain-intrinsic-size: 500px;
}

/* contain для изоляции */
.card { contain: layout style; }

/* Минимизация layout shift */
* { box-sizing: border-box; }
img, video { max-width: 100%; height: auto; }
table { table-layout: fixed; }
```

---

## 25. Референсы (изучать!)

**Продукты с превосходным дизайном (разбирать DevTools):**
- Linear — linear.app (тёмная тема, моушн, типографика)
- Vercel — vercel.com (компоненты, системность)
- Stripe — stripe.com (микрокопирайтинг, иерархия)
- Apple — apple.com (scroll-storytelling, композиция)
- Raycast — raycast.com (анимации, взаимодействия)
- Rauno — rauno.me (портфолио, детали)
- Emil Kowalski — emilkowalski.com (микро-взаимодействия)
- Posthog — posthog.com (юмор, контент)

**Портфолио для вдохновения:**
- brittanychiang.com
- joshwcomeau.com
- leerob.io
- bradfrost.com

**Студии:**
- Cuberto — cuberto.com
- Locomotive — locomotive.ca
- Lusion — lusion.co

---

## 26. Характер в дизайне: практические приёмы

Этот раздел — ответ на реальную ошибку: когда сайт технически сделан хорошо (токены, адаптив, анимации), но feels generic и безликий. Разница между «сделано» и «запоминается».

### 26.1 Визуальный язык = система правил, а не набор токенов

У тебя должны быть **жёсткие правила**, которые проходят через весь интерфейс. Не просто цвета, а поведение линий, теней, углов.

**Примеры визуальных правил (выбери для проекта одно):**

| Система | Правила | Впечатление |
|---------|---------|-------------|
| **Street-authentic** | `border: 2px solid`, `box-shadow: 8px 8px 0`, `rotate(-1.6deg)`, `border-radius: 0` или `14px` | Брутально, честно, физически |
| **Editorial premium** | `border: none`, тонкая тень в 1 слой, крупная типографика, `border-radius: 0`, `text-wrap: balance` | Дорого, журнально |
| **Tech-craft** | `border: 1px solid`, `border-radius: 12px`, hover = translateY(-2px) + тень, openType-фичи | Аккуратно, современно |
| **Soft organic** | `border-radius: 16px`, `box-shadow` многослойная, скруглённые углы везде, `font-feature-settings: "ss02"` | Мягко, дружелюбно |
| **Retro-futur** | `border: 2px dashed`, `box-shadow: 4px 4px 0`, моноширинный акцент, `filter: saturate(1.2)` | Игриво, характерно |

**Ключ:** после выбора — применяй правило ВЕЗДЕ. Кнопки, карточки, инпуты, модалки, футер — все подчиняются одной системе. Если border-radius везде 0 — он везде 0. Если тень 8px 8px 0 — она у всех карточек.

### 26.2 Секции должны быть разными (Background variation)

Самая частая ошибка — все секции выглядят одинаково: один фон, один акцентный цвет, одинаковая структура.

**Правило:** каждая крупная секция меняет фон, настроение, ритм.

```
❌ Ошибка: фон #0a0a0a → #0a0a0a → #0a0a0a → #0a0a0a — монотонно
✅ Хорошо: тёмный → тёмный со stats → бежевый (меню) → красный (процесс) → тёмный (отзывы) → светлая форма
```

**Варианты фонов для разных секций:**
- Нейтральный (`--surface-page`)
- Акцентный (фирменный цвет, 60-80% насыщенности)
- Контрастный инвертированный (в тёмной теме — светлая вставка и наоборот)
- С паттерном/текстурой (шум, полоски, точки)
- С mesh-градиентом (только фон, не мешать контенту)
- С watermark-текстом (крупный полупрозрачный символ/лого на фоне)

**Минимальная смена:** хотя бы 1 секция на акцентном фоне и 1 контрастная (инвертированная). Иначе пользователь не чувствует, что страница «дышит».

### 26.3 Тактильные / физические элементы

Приёмы, которые создают ощущение, что сайт можно потрогать:

```css
/* Штамп (стикер на угле) */
.stamp {
  position: absolute;
  top: -22px; right: -22px;
  width: 96px; height: 96px;
  border-radius: 50%;
  background: var(--accent-yellow);
  border: 3px solid var(--ink);
  display: flex; align-items: center; justify-content: center;
  text-align: center;
  font-family: var(--font-display); font-weight: 900; font-size: 12px;
  transform: rotate(12deg);
  box-shadow: 4px 4px 0 var(--ink);
}

/* Ценник / бирка */
.price-tag {
  position: absolute;
  left: -18px; bottom: 24px;
  background: var(--paper);
  border: 2px solid var(--ink);
  box-shadow: 4px 4px 0 var(--ink);
  padding: 8px 14px;
  font-family: var(--font-display);
  font-weight: 700; font-size: 12px;
}

/* Стикер с поворотом */
.tag-rotate {
  transform: rotate(-3deg);
  background: var(--accent-yellow);
  border: 2px solid var(--ink);
  padding: 5px 11px;
  font-weight: 900; font-size: 10px;
  letter-spacing: .1em; text-transform: uppercase;
}

/* Повёрнутая карточка отзыва */
.review-card {
  transform: rotate(-1.6deg);
  transition: transform 0.25s ease;
}
.review-card:hover {
  transform: rotate(0) translateY(-4px);
}
```

**Когда использовать:** для проектов с физическим продуктом (еда, одежда, мероприятия, ручная работа). Для SaaS/dev-tools — в умеренных дозах (только штампы/стикеры, без бумажной текстуры).

### 26.4 Бренд-нейминг и копирайтинг как часть дизайна

Название и текст — такой же элемент дизайна, как шрифт или цвет.

**Правила имени:**
- Должно быть **произносимо** и **запоминаемо** после одного прочтения
- Хорошо, если есть игра слов, культурный референс или эмоция
- «ОППА РАМЁН» запоминается, «SEOUL RAMYEON» — нет. Почему? «Оппа» — корейское обращение с культурным контекстом, вызывает улыбку

| Слабое имя | Сильное имя | Почему |
|------------|-------------|--------|
| Seoul Ramyeon | Oppa Ramyeon | Культурный якорь + эмоция |
| Cloud Storage | CloudBox | Просто, запоминаемо |
| Task Manager | GoodTask | Характер в названии |
| AI Assistant | Atlas | Бренд-имя, не generic |

**Правила копирайтинга:**
- **Конкретные цифры > абстрактные эпитеты.** «Бульон варим 12 часов» > «наваристый бульон». «Кимчи бродит 21 день» > «домашнее кимчи».
- **Детали процесса создают доверие.** «Тянем лапшу каждое утро — она живёт всего сутки» — человек понимает, что продукт настоящий.
- **Говори от первого лица**, где уместно: «Мы построили это, потому что…»
- **Избегай шаблонных фраз** — «уютная атмосфера», «широкий ассортимент», «индивидуальный подход». Они не несут информации.

**Чеклист копирайтинга для секции:**
- [ ] В hero есть цифра или конкретный факт (не лозунг)
- [ ] В описании продукта/услуги есть технологическая деталь (как сделано, из чего, сколько)
- [ ] Нет слов «инновационный», «уникальный», «best-in-class», «передовой»
- [ ] Если убрать название бренда из hero — всё ещё понятно, о чём сайт?

### 26.5 WOW-элемент: тот самый «chef's kiss»

Этот элемент — то, что гость сфотографирует и пошлёт другу. Без него сайт — просто «ещё один лендинг».

**Варианты WOW-элементов (выбери 1-2):**

1. **Вращающийся SVG-бейдж** — круг с текстом по контуру и иконкой в центре. Использовать на hero, накладывая на изображение. Анимация: `@keyframes spin { to { transform: rotate(360deg) } }`
2. **Floating-частицы** — символы темы (корейские буквы, геометрические фигуры, иероглифы) парят по экрану с разной скоростью. CSS-only через анимацию с разными `animation-duration` и `animation-delay`.
3. **Неоновая вывеска с flicker** — заголовок с мерцанием как у настоящей неоновой трубки:

```css
@keyframes neon-flicker {
  0%,19%,21%,23%,25%,54%,56%,100% {
    text-shadow:
      0 0 7px var(--accent),
      0 0 10px var(--accent),
      0 0 21px var(--accent),
      0 0 42px var(--accent-glow);
    opacity: 1;
  }
  20%,24%,55% {
    text-shadow: none;
    opacity: .7;
  }
}
```

4. **Пар/туман CSS-only** — semi-transparent размытые эллипсы, поднимающиеся с разной скоростью:

```css
@keyframes steam-rise {
  0%   { transform: translateY(0) scale(1); opacity: 0; }
  15%  { opacity: .6; }
  50%  { transform: translateY(-80px) scale(2); opacity: .4; }
  100% { transform: translateY(-160px) scale(3); opacity: 0; }
}
```

5. **Кастомный курсор** — заменяет стандартный курсор на брендированный (для проектов, где важна fidgetability)
6. **Градиентный orb на фоне** — живое пятно, медленно меняющее позицию (смотри раздел 3.3)
7. **Многоколоночная цитата** во всю высоту экрана — editorial приём

**Критерий выбора:** WOW-элемент должен быть **тематически обоснован**. Для раменной — пар и неон. Для SaaS — orb и cursor. Для портфолио — параллакс и stagger-animations.

### 26.6 Конкретная ошибка: разбор Before/After

Вот как выглядит типичная ошибка «сделано, но безлико» и её исправление.

**Проблема:** все секции одинаково тёмные, контент generic, нет запоминающегося элемента, шрифты без характера.

| Аспект | Before (безлико) | After (характерно) |
|--------|------------------|-------------------|
| **Визуальный язык** | `border-radius: 16px`, `box-shadow` по умолчанию, мягкие переходы | `border: 2px solid`, `box-shadow: 8px 8px 0 var(--ink)`, повороты 1-2deg |
| **Шрифты** | Google Fonts по умолчанию (Noto Sans, Black Han Sans) | Unbounded (self-hosted) + Golos Text — редкие, с характером |
| **Секции** | Все на `#0a0a0a` | Чередование: бежевый → бежевый → красный → тёмный → светлый |
| **Копирайтинг** | «Наваристый бульон», «домашняя лапша» | «Бульон 12 часов», «кимчи 21 день», «лапша живёт сутки» |
| **WOW-элемент** | Spice Meter (интерактивный, но не визуальный) | Rotating SVG badge + floating-частицы + неоновый flicker |
| **Детали** | Плавные тени, стандартный hover | Физические штампы, стикеры, повёрнутые карточки отзывов |
| **Функции** | Только отображение | Форма брони с валидацией + счётчик заказа + фильтр меню |
| **Имя** | «SEOUL RAMYEON» (переводится, но не запоминается) | «ОППА РАМЁН» (культурный референс, улыбка) |
| **Карта** | Leaflet (технически круто) | Только адрес (не нужно, если не ядро продукта) |

**Вывод:** техническая сложность (карта, spice meter) не спасает, если фундамент (дизайн-система, копирайтинг, характер) слабый. Сделай базу правильно — детали достроятся.

### 26.7 Landing page: минимальная структура, которая работает

Для сайта одной страницы (кафе, сервис, продукт) используй эту последовательность:

1. **Ticker/marquee** — бегущая строка с ключевыми фактами (до работы, фишки, часы). Задаёт тон сразу.
2. **Nav** — липкая, минимальная (лого + 4-5 ссылок + CTA)
3. **Hero** — крупная типографика, эстетический якорь, 1 CTA, stats/факты
4. **Stats bar** — 3-4 цифры с конкретикой (не «±500 клиентов», а «12 450 чаш рамена за месяц»)
5. **Menu/Products** — карточки с реальными изображениями, ценами, деталями
6. **Process / About** — секция с историей, производством, деталями. Лучше на контрастном фоне.
7. **Atmosphere / Gallery** — визуалы с подписями, создающие настроение
8. **Reviews** — 3 отзыва с настоящими именами и источниками
9. **Booking / Contact** — форма + контакты (или только контакты, если форма не нужна)
10. **Footer** — лого, соцсети, часы/адрес

**Важно:** каждая секция должна быть на РАЗНОМ фоне или иметь визуальный разделитель. Иначе страница сливается в один блок.
