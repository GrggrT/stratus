# План: убрать фактор удачи из результатов STRATUS

Status: внедрено 2026-07-12 (P0–P4). Декомпозиция skill/luck вычисляется из
CLV без миграций; maker-симуляция заменила taker-исполнение в paper;
shrinkage к рынку взвешивается по sample_n калибровки; live-гейт использует
формальный чеклист (см. `calculate_live_gate`). Отчётность:
`/api/v1/performance-summary`, `/api/v1/edge-report`,
`/api/v1/brier-report`, weekly Telegram edge-report (пн 08:30 Berlin).
PIT-валидация — в health-аудите (`pit_miscalibrated`). Примечание: до
заполнения `calibration` (bootstrap IEM + previous runs + recalibrate)
shrinkage удерживает edge около нуля и новые сигналы скипаются с
`station_uncalibrated` — это ожидаемое поведение.

Last updated: 2026-07-12.

## Контекст

Paper-прогон 9–11 июля 2026 показал:

- 24 закрытые позиции;
- realised PnL `+$102.97`;
- ROI `+15.9%`;
- без одной хвостовой сделки KLGA 2026-07-10 результат `-$101.65`;
- robust ROI `-16.7%`;
- средний CLV около нуля;
- CLV beat rate `37.5%`.

Вывод: система технически работает, но торговый edge не доказан. Текущий плюс
может быть дисперсией: система платит спред и выигрывает за счёт одного
outlier-исхода. Цель этого плана — отделить навык от удачи, снизить стоимость
исполнения, ограничить хвостовой риск и формализовать критерий "edge доказан".

Все изменения должны быть:

- mode-aware для `paper` и `live`;
- покрыты unit-тестами чистых функций;
- покрыты integration-тестами на Postgres для новых skip reasons, лимитов,
  backfill и gate-логики;
- параметризованы через `Settings`, без hardcode порогов;
- совместимы с текущим fail-closed подходом.

## P0. Измерение: разложить PnL на навык и удачу

Делать первым. Без честного измерения нельзя оценить эффект следующих пунктов.

### 0.1 Декомпозиция PnL по каждой позиции

Для каждой закрытой позиции считать side-adjusted close price — последнюю
доступную рыночную midpoint-цену до cutoff, уже используемую для CLV.

Формулы:

- `skill_pnl = shares * (p_close - p_entry) - fees`
- `luck_pnl = shares * (outcome - p_close)`
- `outcome ∈ {0, 1}`, side-adjusted

Инвариант:

```text
skill_pnl + luck_pnl == pnl_usd
```

Что добавить:

- поля или вычисляемые значения `skill_pnl_usd`, `luck_pnl_usd`;
- backfill для уже закрытых позиций;
- invariant test на закрытых позициях;
- агрегаты в `summarize_live_performance`;
- поля в `/api/v1/performance-summary`.

Главный KPI системы становится `skill_pnl`, а не raw ROI. Если `skill_pnl`
стабильно `<= 0`, edge нет, даже если realised ROI временно положительный.

### 0.2 Bootstrap confidence interval для ROI и skill_pnl

Добавить bootstrap по закрытым позициям:

- 10 000 ресемплов с возвращением;
- deterministic seed;
- percentile CI `2.5% / 97.5%`;
- отдельно для ROI и `skill_pnl`;
- флаг `statistically_positive`, если CI не содержит 0.

Поля в summary:

- `roi_ci_low`;
- `roi_ci_high`;
- `skill_pnl_ci_low`;
- `skill_pnl_ci_high`;
- `statistically_positive`.

### 0.3 Brier модели против рынка

По resolved позициям сравнивать качество модели и рынка на момент сигнала:

```text
brier_model = mean((model_prob - outcome)^2)
brier_market = mean((market_price - outcome)^2)
```

Информационный edge существует только если:

```text
brier_model < brier_market
```

Добавить в summary:

- `brier_model`;
- `brier_market`;
- `n_resolved`.

### 0.4 Калибровка predicted edge

Проверять, превращается ли predicted edge в realised return:

- `realized_return = pnl_usd / cost_usd`;
- bucket predicted edge:
  - `0–3%`;
  - `3–6%`;
  - `6–10%`;
  - `>10%`;
- считать средний realised return по bucket;
- считать slope линейной регрессии.

Интерпретация:

- slope около `1` — edge похож на настоящий;
- slope около `0` — модельный edge похож на шум.

Добавить endpoint:

```text
/api/v1/edge-report?mode=paper|live
```

Дополнительно реализован root-cause endpoint:

```text
/api/v1/brier-report?mode=paper|live
```

Он отдаёт общий Brier model-vs-market и срезы по station/kind/horizon/
confidence/side. Positive `model_minus_market` означает, что модель в этом
срезе хуже рынка.

## P1. Исполнение: перестать платить спред

### 1.1 Edge обязан покрывать стоимость исполнения

Текущий `min_edge` недостаточен, если он не учитывает spread/fee/slippage.

Сигнал должен проходить только если:

```text
edge >= half_spread + fee_rate + edge_cost_buffer
```

где:

- `half_spread = (best_ask - best_bid) / 2`;
- `edge_cost_buffer` — новый параметр `Settings`, default `0.02`.

Иначе:

```text
skip_reason = "edge_below_costs"
```

### 1.2 Maker-симуляция в paper вместо taker ask

Сейчас paper execution фактически платит taker-цену. Это создаёт системный
минус на неликвидных погодных рынках.

Нужна maker-симуляция:

- вместо покупки по ask выставлять paper-limit на midpoint или `best_bid + tick`;
- order получает состояние `paper_resting`;
- fill засчитывается только если следующие price snapshots проходят через наш
  limit в течение TTL, например 30 минут;
- иначе order истекает как `unfilled_maker`;
- fill-rate логировать как отдельную метрику качества исполнения.

Это намеренно уменьшит число исполненных сигналов. Это нормальный ожидаемый
эффект.

### 1.3 Фильтр ширины спреда

Добавить `max_spread`, default `0.05`.

Если:

```text
best_ask - best_bid > max_spread
```

то:

```text
skip_reason = "spread_too_wide"
```

На погодных рынках spread 10–20¢ легко уничтожает любую модельную альфу.

## P2. Сайзинг: одна сделка не должна решать всё

### 2.1 Кап по потенциальному выигрышу, а не только по cost

Текущие лимиты по вложенному капиталу не ограничивают хвостовые payouts.
Пример: позиция за `$37.80` при цене около `0.15` может иметь потенциальный
выигрыш около `$214`, то есть около 20% банкролла.

Добавить:

```text
potential_win = shares * (1 - price)
potential_win <= max_win_fraction * bankroll
```

Новый параметр:

```text
max_win_fraction = 0.02
```

Sizer должен не только skip-ать, а сначала пытаться уменьшить размер до
допустимого potential win.

### 2.2 Фильтр хвостовых вероятностей

Позиции с entry price `< 0.10` или `> 0.90` особенно чувствительны к
калибровочным ошибкам.

Добавить параметры:

- `tail_price_low = 0.10`;
- `tail_price_high = 0.90`.

Если станция/горизонт не откалиброваны, такие сигналы скипать:

```text
skip_reason = "tail_uncalibrated"
```

### 2.3 Консервативнее Kelly на стадии validation

Проверить текущий Kelly multiplier/cap и сделать multiplier параметром.

Для validation-stage:

```text
kelly_multiplier = 0.25
```

Меньше размер — меньше дисперсия и быстрее статистическая интерпретация на том
же банкролле.

## P3. Калибровка: торговать только доказанное

### 3.1 Gate по объёму калибровочной выборки

Станция/вид рынка/горизонт считаются откалиброванными, когда соответствующая
строка `calibrations` имеет:

```text
sample_count >= min_calibration_samples
```

Default:

```text
min_calibration_samples = 30
```

Для неоткалиброванных станций сигналы можно генерировать и сохранять, но не
исполнять:

```text
skip_reason = "station_uncalibrated"
```

Так данные продолжают копиться, но деньги не рискуются.

### 3.2 Shrink probability к рынку при тонкой выборке

Вместо бинарного gate использовать плавное сжатие модели к рынку:

```text
p_used = w * p_model + (1 - w) * p_market
w = n / (n + n0)
n0 = 30
```

Смысл: пока модель не заслужила доверие выборкой, она почти не отклоняется от
рынка. Edge стремится к нулю, и лишние сигналы исчезают.

Логировать:

- `p_model_raw`;
- `p_market`;
- `p_used`;
- `calibration_weight`.

Это ключевой anti-luck механизм.

### 3.3 PIT-валидация sigma

Проверять честность forecast distribution:

```text
PIT = CDF_predicted(actual)
```

PIT по дням/станциям должен быть примерно равномерным.

Добавить weekly job или расширить `recalibrate_emos`:

- PIT histogram;
- Kolmogorov–Smirnov p-value;
- alert в Telegram при явной переуверенности или недоуверенности модели.

## P4. Формальный критерий "edge доказан"

### 4.1 Расширить validation/live gate

Поверх legal/tax/wallet gate live должен требовать:

- `n_closed >= 200`;
- bootstrap CI ROI lower bound `> 0`;
- `skill_pnl > 0` и CI по skill_pnl не содержит 0;
- `clv_beat_rate >= 0.52`;
- `brier_model < brier_market`;
- no unresolved `critical` or `blocking` health issues.

Пока критерии не выполнены:

- `/mode live` отклоняется;
- `set_mode live` отклоняется;
- оператор получает список непройденных пунктов.

### 4.2 Отчётность

Добавить:

- `/api/v1/edge-report?mode=paper|live`;
- weekly Telegram report с теми же метриками;
- checklist "что уже доказано, чего не хватает".

Отчёт должен включать:

- skill/luck decomposition;
- ROI and skill_pnl bootstrap CI;
- Brier model vs market;
- edge calibration buckets;
- station/kind/horizon breakdown;
- live gate checklist.

## Приёмка для каждого пункта

1. Unit-тесты чистых функций:
   - decomposition;
   - bootstrap;
   - shrinkage;
   - potential win cap;
   - edge cost check.
2. Integration-тесты на Postgres:
   - новые skip reasons;
   - sizing caps;
   - maker fill/expiry;
   - gate rejection;
   - backfill.
3. Инвариант:

   ```text
   skill_pnl + luck_pnl == pnl_usd
   ```

4. Все новые пороги — параметры `Settings` с безопасными default.
5. Backfill для закрытых позиций.
6. `ruff`, `mypy --strict`, full pytest и CI green.

## Ожидаемый эффект

Сигналов станет заметно меньше. Это правильно.

Цель не в том, чтобы торговать часто. Цель — чтобы каждый исполненный доллар
имел положительное матожидание относительно закрытия рынка, а решение о live
принималось по `skill_pnl` и статистической значимости, а не по raw ROI,
который сейчас может быть сделан одной удачной сделкой.
