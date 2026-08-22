---
name: frontend-design
description: Design, implement, improve, or audit web interfaces. Prioritize truthful content, task clarity, hierarchy, context-appropriate visual character, full states, accessibility, responsive robustness, and visual QA while avoiding generic AI patterns and unnecessary scope.
---

# frontend-design

Создавай интерфейсы, которые выглядят намеренно спроектированными под конкретную задачу, а не собранными из модных приёмов.

Этот skill — **система принятия решений**, не CSS-справочник. Не копируй стиль из примеров механически. Сначала пойми продукт и контекст, затем выбери подходящую форму.

## 0. Приоритеты

Если правила конфликтуют, соблюдай их в этом порядке:

1. задача пользователя и существующие требования;
2. правдивость контента и сохранение scope;
3. понятность, hierarchy и usability;
4. функциональность и состояния;
5. accessibility;
6. responsive robustness;
7. согласованность visual system;
8. character / brand fit;
9. motion и декоративная полировка.

Не жертвуй верхним уровнем ради нижнего.

Красивый выдуманный продукт хуже простого правдивого.
WOW-эффект не компенсирует сломанную форму, мобильную версию или keyboard flow.

---

## 1. Определи режим задачи

Перед работой классифицируй задачу:

- **New UI / landing** — структура, контент, visual language и states с нуля.
- **Product UI / app / dashboard** — task flow, density, data states, predictability.
- **Component** — states, semantics, accessibility, content constraints, system fit.
- **Redesign / polish** — сначала изучи существующую систему; не замещай её собственным вкусом.
- **Audit** — найди проблемы, оцени severity, исправляй от критичного к косметическому.

Не превращай маленький frontend fix в редизайн без запроса.

### Existing system first

Если проект уже существует, сначала определи:

- framework и styling approach;
- tokens;
- fonts;
- palette;
- spacing/radius/border/shadow language;
- icon system;
- component library;
- responsive conventions;
- theme support;
- existing states и accessibility patterns.

**Расширяй существующую систему раньше, чем создаёшь новую.**

Не добавляй новую icon/animation/component library без необходимости.

---

## 2. Design contract: смысл, scope, правда

До визуальной полировки ответь:

1. Кто пользователь?
2. Что он пытается сделать?
3. Какова главная цель страницы/экрана?
4. Какое действие primary?
5. Что должно быть понятно за первые секунды?
6. Какие данные и утверждения реально известны?
7. Какие функции и секции обязательны?
8. Что вне scope?
9. Какой brand/tone задан?
10. Какие assets доступны?
11. Какие ограничения среды есть?
12. Какие states и edge cases вероятны?

Если часть ответов неизвестна, не блокируй работу без необходимости. Делай минимальные разумные допущения и не превращай их в факты.

### Truth gate

Без явного разрешения **не выдумывай**:

- статистику, пользователей, клиентов;
- testimonials, рейтинги, награды;
- цены, адреса, даты;
- product capabilities и интеграции;
- release/episode names;
- business metrics;
- логотипы компаний как social proof.

Если данных нет — убери необязательную секцию, используй нейтральный copy или явно маркированный demo content.

### Scope gate

Не добавляй автоматически:

- pricing;
- newsletter;
- booking;
- login;
- testimonials;
- FAQ;
- карту;
- форму заявки;
- social proof;
- checkout;
- AI-функции;
- дополнительные CTA.

**Design amplifies the product. Design does not invent the product.**

---

## 3. Рабочий процесс

Используй один цикл.

### Step 1 — Understand
Сформулируй одной фразой: кто пользователь, что он должен понять и что сделать.

### Step 2 — Content inventory
Раздели контент на `required / supporting / optional / unsupported`.
Unsupported убери или замени честной формулировкой.

### Step 3 — Information architecture
Определи порядок чтения, секции, primary action, navigation, data hierarchy и relationships.
Каждая область должна иметь понятную работу.

### Step 4 — Structural pass
Собери семантику и layout skeleton.

Не обязательно физически писать весь HTML без классов; важно, чтобы смысл и порядок не зависели от декоративного CSS.

Для content-heavy страницы текст должен оставаться логичным без стилей.
Для app UI должны оставаться понятны controls, labels, regions и порядок взаимодействия.

### Step 5 — Visual thesis
Опиши дизайн тремя конкретными прилагательными и зафиксируй:

- typography character;
- color logic;
- surface/border language;
- imagery strategy;
- motion character.

«Современный, красивый, минималистичный» — не направление.

### Step 6 — System + states
Определи только нужные type/color/spacing/layout/radius/border/elevation rules и проработай реальные states.

### Step 7 — Responsive + stress
Проверь изменение ширины, длины текста, количества данных, zoom и missing content.

### Step 8 — Visual QA + subtraction
Если среда позволяет: `implement → render → inspect → fix → render again`.

После этого намеренно удали лишнее.

---

## 4. Visual hierarchy и композиция

На каждом viewport должен быть один главный focal point.

Иерархия строится через:

- size;
- weight;
- contrast;
- position;
- whitespace;
- color;
- imagery;
- motion.

Не усиливай каждый элемент всеми способами сразу.

### Правила

- Если всё акцентное — ничего не акцентно.
- Primary action визуально сильнее secondary.
- Заголовок, visual и CTA не должны бороться за первое место.
- Negative space — инструмент группировки, не пустота.
- Density должна соответствовать задаче.
- Для частых операций efficiency важнее декоративного воздуха.
- Для storytelling можно позволить крупнее scale и больше пауз.
- Symmetry даёт спокойствие, asymmetry — энергию; ни одно не лучше без контекста.
- Break the grid only intentionally.

### Ритм секций

Не используй одну композицию для каждой секции, но и не меняй фон каждой секции ради разнообразия.

Ритм можно менять через:

- scale;
- density;
- whitespace;
- alignment;
- text width;
- grid direction;
- media placement;
- background;
- typography contrast;
- overlap;
- repetition with variation.

Секция должна отличаться настолько, насколько отличается её функция.

Если две соседние секции делают одно и то же — возможно, их надо объединить.

---

## 5. Visual language и character

Характер — система, а не набор эффектов.

Зафиксируй поведение:

- borders;
- radii;
- elevation/shadows;
- typography;
- imagery;
- accent usage;
- motion.

Не смешивай случайно несовместимые языки: например hard brutalist shadows и soft glass cards без общей идеи.

### Возможные направления

- **Editorial** — typography-led, сильный scale, disciplined whitespace.
- **Industrial / utility** — высокая density, функциональные borders, минимум motion.
- **Playful** — выразительные shapes/illustrations, заметный feedback.
- **Street / tactile** — physical borders, hard shadows, controlled irregularity.
- **Quiet premium** — точные пропорции, restrained palette, сильные материалы/изображения.
- **Tech-craft** — точная сетка, тонкие borders, functional mono accents, controlled motion.

Это не presets. Выбирай логику, а не готовый внешний вид.

### Signature element

Запоминающийся элемент **не обязателен**.

Он чаще полезен для marketing/editorial/entertainment/portfolio и часто не нужен для settings/admin/enterprise utility UI.

Если используешь:

- он тематически обоснован;
- не конкурирует с content/CTA;
- не ухудшает accessibility/performance;
- уважает reduced motion;
- не повторяется как gimmick.

Один сильный жест лучше пяти трюков.

---

## 6. Typography, color, geometry

### Typography

- Выбирай fonts по характеру, читаемости, glyph coverage, лицензии и контексту.
- Не выбирай и не запрещай font только из-за его популярности у AI.
- Обычно хватает одного семейства; display + body — если контраст нужен; mono — для кода/данных.
- Размеры должны образовывать ясную hierarchy, но не обязаны следовать одной математической ratio.
- Fluid sizing используй там, где он реально помогает.
- Display text обычно требует tighter leading; body — комфортного line-height.
- Сохраняй readable line length.
- Не разрушай текст чрезмерным tracking, uppercase или слишком узким measure.

Для русского: «ёлочки», „лапки“ внутри, тире —, диапазоны –; избегай висячих коротких предлогов там, где это уместно.

### Color

Определи роли:

- page/background;
- surface/elevated surface;
- primary/secondary text;
- borders;
- accent;
- success/warning/error/info при необходимости.

Правила:

- essential text проходит WCAG AA;
- цвет не единственный носитель состояния;
- один dominant accent часто достаточно, но это не закон;
- дополнительные цвета имеют роль;
- не добавляй случайный CTA-color;
- dark theme делай только если она нужна или уже существует;
- OKLCH полезен, но не является признаком качества сам по себе;
- не переписывай рабочую palette только ради другого color space.

Избегай generic purple/blue glow как shortcut для «tech», если это не бренд.

### Spacing / grid / geometry

- Используй ограниченный набор повторяемых spacing steps.
- 4/8px baseline — хороший default, не закон.
- Optical correction 1–3px допустима осознанно.
- Связанные элементы ближе, несвязанные — дальше.
- Grid выбирается под content; 12 columns не обязательны.
- Radius/border/shadow должны образовывать одну grammar.
- Elevation levels должны обозначать реальную hierarchy, а не просто «делать красиво».

---

## 7. Content, imagery и media

### Copy

Конкретика обычно сильнее абстракции, **только если она правдива**.

Предпочитай:

- действие вместо абстрактного существительного;
- понятный результат вместо marketing adjective;
- product evidence вместо generic promise.

Hero не обязан содержать цифру.

Он обязан быстро объяснять:

- что это;
- почему важно;
- что делать дальше.

Buttons должны описывать действие: `Save changes`, `Create project`, `Try again`, `Download report`.

### Images

Сначала используй assets пользователя/проекта.

Не вставляй случайное stock image только чтобы заполнить hero.

Изображение должно:

- показывать продукт;
- доказывать качество;
- объяснять функцию;
- задавать атмосферу;
- давать контекст;
- поддерживать историю.

Проверяй relevance, license/source, crop, focal point, resolution, aspect ratio, alt и layout shift.

Не lazy-load главный LCP visual автоматически.

---

## 8. Components и state coverage

Для поведения предпочитай native semantics и существующую accessible component library.

Не собирай интерактивный control из `div`, если `button`, `a`, `input`, `select`, `textarea`, `details` или `dialog` решают задачу.

### Interactive states

Проверь релевантные:

- default;
- hover;
- focus-visible;
- active/pressed;
- selected/current;
- disabled;
- loading;
- success;
- error/invalid;
- readonly;
- visited для ссылок, если полезно.

Не каждый state нужен каждому компоненту.

### Data/container states

Проверь релевантные:

- loading;
- empty;
- partial;
- populated;
- error;
- stale/offline;
- permission denied.

### Content stress

Проверь:

- короткий текст;
- длинный текст;
- long word/URL;
- missing optional value;
- большие числа;
- 1 item;
- много items;
- missing image.

### Empty / error / success

**Empty:** объясняет, что здесь появится и что делать дальше.
**Error:** говорит, что случилось и как продолжить.
**Success:** подтверждает результат и следующий шаг, если он есть.

Не оставляй пользователя гадать, завершилось ли async action.

---

## 9. Accessibility — часть дизайна

Не откладывай её на конец.

Минимум:

- semantic HTML;
- корректная heading hierarchy;
- keyboard operability;
- logical tab order;
- visible `focus-visible`;
- достаточный contrast;
- labels и accessible names;
- alt text;
- errors связаны с fields;
- status/error feedback доступен assistive tech;
- состояние не передаётся одним цветом;
- reduced motion;
- разумные touch targets.

Для overlays/dialogs/menus продумай:

- opening;
- initial focus;
- Escape;
- closing;
- return focus;
- background interaction;
- screen reader semantics.

Не выдавай визуально красивую CSS-only имитацию за production-ready interactive component.

---

## 10. Motion

Motion должен:

- подтверждать действие;
- показывать state change;
- объяснять появление/исчезновение;
- показывать spatial relationship;
- направлять внимание;
- усиливать storytelling, когда это уместно.

Не добавляй motion только потому, что интерфейс кажется «пустым».

### Timing

Ориентиры:

- press/hover feedback — очень быстро;
- tooltip/dropdown/state changes — быстро;
- modal/drawer — умеренно;
- page/section storytelling — может быть дольше.

Frequently-used UI не должен ощущаться медленным.

Для performance предпочитай `transform` и `opacity`; не анимируй layout properties без причины.

Существенная decorative animation должна иметь reduced-motion вариант.

---

## 11. Responsive, stress и performance

Responsive — не список магических breakpoint numbers.

Ставь breakpoint там, где ломается composition или usability.

Проверь:

- narrow mobile;
- обычный mobile;
- tablet-ish width;
- desktop;
- wide desktop, если layout это использует;
- 200% zoom;
- increased text size.

Проверяй:

- no horizontal scroll;
- readable line length;
- touch targets;
- sticky UI не закрывает content;
- controls не становятся слишком плотными;
- tables имеют осознанную mobile strategy;
- dialogs/popovers помещаются;
- image focal point сохраняется;
- long labels не ломают layout.

Не превращай любую mobile modal автоматически в bottom sheet.

### Performance guardrails

Без причины избегай:

- huge background video;
- heavy blur на больших областях;
- десятков continuous animations;
- oversized images;
- лишних font files/weights;
- layout-thrashing animation;
- новой dependency ради маленького эффекта.

Hero visual может быть LCP; below-the-fold media можно lazy-load; размеры media должны уменьшать layout shift.

---

## 12. Generic / AI-slop detector

Это **risk detector**, не список запретов.

Если несколько пунктов совпали без сильного контекстного обоснования — пересмотри дизайн:

- hero можно отдать конкуренту почти без изменений;
- gradient/orb заменяет настоящий content/visual;
- три одинаковые feature cards с иконками;
- банальный `How it works: 1–2–3`;
- декоративные `01 / 02 / 03` у непоследовательных элементов;
- fake stats/testimonials/social proof;
- FAQ из выдуманных вопросов;
- final CTA просто повторяет hero;
- все секции имеют одну композицию;
- каждый card одинаково «подпрыгивает» на hover;
- serif используется только для псевдо-premium;
- glassmorphism без связи с brand;
- случайный purple/blue glow;
- badges/pills над каждым heading;
- stars/sparkles повсюду;
- oversized text без информационной причины;
- несколько декоративных эффектов конкурируют одновременно.

### Лекарство

Не «добавить ещё декора».

Сначала ищи:

- более конкретный content;
- реальный product visual;
- сильнее composition;
- точнее typography;
- правильнее density;
- характерные assets;
- осмысленную visual metaphor.

---

## 13. Guidance по типу продукта

### Landing / marketing

Приоритет: `message → evidence → identity → narrative rhythm → CTA`.

Не используй фиксированную структуру `ticker → stats → features → testimonials → CTA` как default.

### Product UI

Приоритет: `task completion → information density → state clarity → predictability → keyboard efficiency → polish`.

Не превращай каждую сущность в огромную marketing card.

### Dashboard

Приоритет: `hierarchy → comparable data → scanability → filtering/sorting → states`.

Не делай stat cards только потому, что dashboard «так выглядит».

### E-commerce

Приоритет: `imagery → price/variant clarity → availability → comparison → purchase action → trust`.

Decorative layout не должен мешать просмотру товара.

### Portfolio / editorial

Можно сильнее использовать typography, art direction, asymmetry и motion, но readability остаётся выше novelty.

---

## 14. Audit mode

При работе с существующим UI:

### Observe

Изучи реальные screens/components, tokens, repeated patterns, responsive, states, content и accessibility.

Если доступен render/screenshot — смотри на него, а не только на source.

### Classify

**P0 — блокирует сдачу**

- функция сломана;
- critical content не читается;
- navigation/keyboard flow недоступен;
- focus отсутствует;
- важный contrast неприемлем;
- layout ломается;
- fabricated content вводит в заблуждение;
- mobile unusable.

**P1 — серьёзно ухудшает**

- слабая hierarchy;
- inconsistent components;
- missing states;
- плохая density;
- confusing CTA;
- generic repetition;
- weak responsive behavior.

**P2 — polish**

- optical alignment;
- micro-motion;
- subtle color;
- decorative texture;
- minor spacing.

Fix: `P0 → P1 → P2`.

Не делай 30 polish-изменений, пока остаётся серьёзная usability-проблема.

---

## 15. Visual QA и subtraction pass

Если среда позволяет увидеть интерфейс, этот этап обязателен.

### Desktop

Проверь:

- куда глаз падает первым;
- понятна ли страница без чтения каждого слова;
- конкурируют ли focal points;
- alignment;
- repeated cards;
- dead space;
- hero density;
- image crops;
- generic/template feeling.

### Mobile

Проверь:

- order;
- wrapping;
- CTA/navigation;
- touch;
- crop;
- overflow;
- sticky elements;
- dialogs;
- forms.

### States

Проверь хотя бы основные: focus, hover, active, loading, empty, error, success, disabled.

### Subtraction

Для каждого декоративного элемента спроси:

- помогает hierarchy?
- поддерживает brand?
- несёт информацию?
- создаёт нужный rhythm?

Если нет — убери.

Также убери:

- redundant section;
- repeated pattern;
- unsupported claim;
- second focal point;
- animation без задачи.

После существенных изменений render снова.

---

## 16. Quality gates

### P0 — must pass

- [ ] Решена именно задача пользователя.
- [ ] Существенный scope не добавлен без основания.
- [ ] Нет выдуманных фактов/метрик/social proof.
- [ ] Главная цель понятна.
- [ ] Primary action очевиден, если он нужен.
- [ ] Интерфейс функционален.
- [ ] Основной keyboard flow работает.
- [ ] Focus видим.
- [ ] Essential text читаем и контрастен.
- [ ] Layout не ломается на целевых размерах.
- [ ] Нет critical horizontal overflow.
- [ ] Существенная animation учитывает reduced motion.

### P1 — should pass

- [ ] Hierarchy сильная.
- [ ] Visual language согласован.
- [ ] Typography и density соответствуют контексту.
- [ ] Colors имеют роли.
- [ ] Spacing создаёт понятную группировку.
- [ ] Основные states существуют.
- [ ] Long/empty/error content не ломает UI.
- [ ] Mobile — самостоятельная композиция, а не уменьшенный desktop.
- [ ] Нет очевидного generic AI composition.
- [ ] Existing system не разрушен без причины.

### P2 — polish

- [ ] Optical alignment аккуратен.
- [ ] Motion уместен.
- [ ] Images/crops качественные.
- [ ] Microcopy точный.
- [ ] Decorative details поддерживают, а не перегружают.
- [ ] После subtraction pass не осталось очевидно лишнего.

---

## 17. Правила можно нарушать

Taste rules — эвристики, не законы.

Можно нарушить guideline, если:

- причина понятна;
- решение поддерживает задачу/brand;
- оно последовательно;
- не ухудшает usability;
- не нарушает accessibility;
- его можно объяснить одной короткой фразой.

Нельзя оправдывать «характером»:

- fake data;
- inaccessible controls;
- invisible focus;
- unreadable essential text;
- broken responsive;
- broken semantics;
- unpredictable behavior.

Ни одна технология или эстетика не делает дизайн хорошим автоматически: OKLCH, 12-column grid, gradients, dark mode, serif, giant type, animation, parallax, noise, 3D, brutal shadows, perfect 8px spacing, сотни tokens — всё это только инструменты.

---

## Финальный принцип

Не стремись сделать интерфейс «не похожим на AI» любой ценой — это тоже превращается в шаблон.

Стремись сделать его:

- конкретным;
- правдивым;
- подходящим контексту;
- ясным;
- устойчивым к реальным данным;
- визуально цельным;
- достаточно характерным, но не переигранным;
- проверенным глазами, а не только кодом.

**Сильный дизайн выглядит не так, будто дизайнер применил много правил, а так, будто решения естественно следуют из задачи.**
::: ​​