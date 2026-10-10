# STRATUS: текущая цель и границы разрешений

Обновлено: 2026-09-26. Задача Codex: `019f334d-446c-7531-a848-14ea839be0b4`.

Уточнение при возобновлении работы 2026-09-26: полное objective корректно и не
сокращается; обновлена только ближайшая code-only очередь ниже. Карточка цели
повторно прочитана после возобновления: `active`, не `complete`.
В текущих исходниках написаны P/W terminal-handshake, локальная compiler/input
closure и новый удерживаемый L coordinator с actual held-output owner. Это
незавершённая интеграция, не принятый checkpoint: актуальные C#/native и
реальный terminal-протокол не исполнялись; полная независимая приёмка, действующий writer
scope, внешнее ограничение lifetime L и success sealing/output acceptance
отсутствуют. Qualification gates остаются deny-all, selectors — null.
Исторические PASS ниже не относятся ко всей изменённой terminal-source.

В текущем продолжении отделены shared terminal DTO/wire от прямых P-only
compiler/artifact зависимостей; typed result adapter остаётся у исходного P owner.
Все актуальные peer ABI producers/consumers согласованы на P v6 / W v8, generic
Bootstrap v2 и прежние bounds сохранены. Добавленные coordinator/capture API имеют
отдельный v1. Статическая карта 31 C# файлов описывает inclusive closure W11/L26/P27,
но не доказывает успешную сборку. Новая L-цепочка удерживает исходные P/W owners,
требует writer scope до preload/P birth, читает actual output после normal closure
и сохраняет все acceptance/import/publication flags false. Отдельный STOP-path
не заменяет success sealing. Полная матрица отказов ещё не принята. См. текущий
[прогресс](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-protocol-20260926/PROGRESS.md).
Ограниченный независимый обзор новой L/output цепочки завершён: исправлены
два конкретных дефекта (exact loader field table и post-close capability getter),
других блокирующих замечаний в этой области не найдено. Это не полный terminal
review и не test-safety разрешение; [границы обзора](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-protocol-20260926/REVIEW-L-OUTPUT.md).

В текущем code-only продолжении адаптированы восемь прежних source suites:
сохранены все исторические IDs/порядок и отрицательные проверки, теперь 992 cases.
Они прошли bounded независимый cross-review, но ещё не исполнялись и не имеют
нового test-safety разрешения. Добавлен отдельный набор L/output из 102 source
cases. Его первая PS5-попытка остановилась на ошибке тестового lexer: правила C#
были ошибочно применены к PowerShell single-quoted backslash. Неудачная попытка
сохранена, PS7 по прежнему разрешению не запускался. Исправлен только harness;
после нового независимого решения R2 прошли PS5 attempt 2 и PS7 attempt 1:
102/102 на каждой оболочке, 204 source cases всего, без skipped/failed и с
неизменными 15 внешними guards до/после каждого успешного прогона. Это не runtime
приёмка; [результаты и ограничения](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-output-source-20260926/RESULTS.md).
Независимый аудит сохранённых доказательств завершён без расхождений; итоговый
индекс содержит 12 файлов, первая неудачная попытка не скрыта. Окно R2 закрыто;
не повторять неизменённый успешный набор вместо следующей code-only интеграции.
Дополнительно исправлены peer
InputCount сверка с original admitted context и acquire-порядок публикации W
normal-drain; прежние pin обзора L остаются историческими для этих изменённых файлов.
Обзор P/W выявил незакрытый acceptance blocker: успешный возврат `IDisposable.Dispose`
не доказывает успешный native close. Требуется переработать original input/compiler
IO ownership с удерживаемыми исходами native acquire/close; нельзя закрывать raw
handle поверх owning SafeHandle, ослаблять generic input validation или выдавать
managed-return за native-release proof. Полная приёмка протокола по-прежнему закрыта.

Текущая code-only работа конкретизирована в
[плане native-close ownership](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-protocol-20260926/NATIVE-CLOSE-REDESIGN-PLAN.md):
сначала original file acquisition и typed executable borrowing без ослабления
generic проверки, затем исходные stdio-pairs/ранние close, settlement и release.
Реализация этого ограниченного участка уже начата: `Inputs.cs` хранит original
acquisition/native-close отдельно от managed disposal и выдаёт exact typed borrow;
`Bootstrap.cs` использует ту же модель для executable lock и stdio.
Независимый обзор исправил две failure-edge ошибки в Inputs: только точный `-1`
является известным no-handle результатом CreateFile, а неожиданно закрытая/
инвалидированная wrapper до первого native close требует quarantine. Ограниченный
[независимый source-review](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-protocol-20260926/REVIEW-NATIVE-OWNERSHIP.md)
сохранён: других блокирующих замечаний в проверенном участке нет, девять pins
перечитаны без расхождений. Это не проверка установленного native runtime.
Ownership-набор адаптирован: 180 объявленных cases, включая все 59 исторических
пар и два BCL fixtures; независимый обзор покрытия ещё идёт. Birth-job source-suite
сохраняет 31 историческую пару и проверяет новый raw-pair путь. Нового прогона, компиляции и native acceptance
нет. Это не принятие всего terminal-протокола; отдельные owning pipes L/Guardian
и старого ParentStage runtime остаются вне этого участка. Текущий статус и очередь:
[native-resource checkpoint](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/native-resource-ownership-20260926/STATUS.md).

По прямому запросу пользователя удалена только периодическая проверка
`stratus-paper-guard`; это не отменяет защитные ограничения и не возобновляет сервисы.
Ранее карточка была `paused`; после возобновления её текущий статус проверен как
`active`. Изменение этого документа само по себе не переключает авторежим.
Удалённая периодическая проверка не восстановлена.

Предыдущий source-checkpoint: связана code-only цепочка actual Job ACK → original suspended compiler birth →
передача exact root/Job → actual root ACK → guarded Resume и transfer-aware STOP.
Независимый source-review не оставил блокирующих замечаний; все qualification
blockers остаются deny-all. По новому независимому test-safety freeze прошли
1408/1408 source-проверок на каждой PS5/PS7, 2816 всего, с первой попытки.
Из них 182 — новые направленные root/call-site cases; неизменённый ContextCapture60
и исторические native/Sandbox матрицы не повторялись. C#/native не исполнялись.
Это не приёмка полного terminal, parent/bootstrap-протокола, R0–R9 или инцидента.
[Результаты и границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/compiler-root-handoff-20260925/RESULTS.md).
Далее — полный terminal с двухсторонним private EOF, physical P/W exit, actual input
recapture и независимо заданным output owner; не считать локальный compiler result
успехом всей сборки. Полная цель остаётся active, разрешения не расширены.

Завершено прерванное закрытие доказательств Job-transfer checkpoint: независимый
аудит сохранённых 24 журналов завершён, 40 артефактов включены в итоговый индекс;
39 прежних файлов доказательств и 88 исходных файлов при закрытии не изменились.
Тесты, C#/native и сервисы в этом продолжении не запускались. Это проверка
сохранённых результатов, не новая runtime-квалификация. Полная цель остаётся active.
[Аудит](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/guardian-compiler-job-transfer-20260923/evidence-audit.md)
и [индекс](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/guardian-compiler-job-transfer-20260923/artifact-index.json).

Новый code-only checkpoint: реальная source-цепочка передачи исходного compiler Job
в W, фактический private ACK и transfer-aware STOP связаны. Новая capability 0x40
отделена от прежних stop rights 0x101001; native transfer и compiler birth остаются
безусловно закрыты. Исправлен найденный независимым review разрыв pending-read
наблюдения в P; STOP теперь требует physical P death, разрешения исхода передачи
и exact Job cleanup. Независимое source review и аудит покрытия выполнены.
Все 1286 проверок исходников на PS5 и PS7 прошли с первой попытки (2572 всего),
1178 прежних cases/порядок сохранены, добавлены 108 направленных проверок.
C#/native не исполнялись. Полная активная цель R0–R9 и разрешения не изменены.
[Доказательства, ограничения и следующий этап](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/guardian-compiler-job-transfer-20260923/RESULTS.md).

Предыдущий source-checkpoint: реальное владение compiler внутри исходного P invocation.
Original entry/identity/clock/private ACK и input phase теперь принадлежат одному
retained объекту; compiler owner и command удерживаются до worker/registration.
Новый environment selector остаётся deny-all, compiler Job preparation имеет
отдельный unconditional blocker, generic birth/Resume для этого режима запрещены.
Независимое source-review завершено без блокирующих замечаний. Все 1020 source/BCL
проверок прошли PS5 и PS7 с первой попытки; прежние 946 case IDs и порядок сохранены.
Три дополнительных пары закрыли найденный до прогона пробел покрытия owner/command/
clock links. C# не компилировался и не исполнялся. Полная цель и разрешения прежние.
[Доказательства и границы результата](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/producer-compiler-owner-20260923/RESULTS.md).

Предыдущий завершённый code-only этап: подготовка original Job отделена от рождения процесса в
реальном CompilerCommand; actual P wrapper использует обе фазы, lifecycle ABI v2.
Создание и настройка имеют отдельные retained outcomes; birth требует завершённой
подготовки и свежего empty/never-used Job. Unknown не разрешает fallback/retry.
После межфазной паузы cwd/executable перепроверяются; known-created Job можно явно
contain/close и при отказе настройки, без ложного требования successful preparation.
Это outer Job самого P, не compiler Job внутри P и не W ACK. Независимый source-review
завершён; шесть закреплённых наборов 120/190/100/84/80/62 прошли PS5/PS7 (636 на
оболочку). Первая PS5-попытка нового теста отказала из-за смешения правил C#/PowerShell
в тестовом tokenizer; исправлен только тест, затем обновлена независимая проверка
безопасности. Исходная неуспешная попытка сохранена, прежние case inventories не
сокращены. C#/native не исполнялись. [Результаты](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/birth-job-preparation-20260923/RESULTS.md).
Ближайшая
реальная цепочка и STOP-зависимости уточнены в «Актуальном задании для продолжения».
Полная активная цель R0–R9 не изменена; production/PAPER остаются отдельно согласуемыми.

Предыдущий code-only этап: первый P continuation связан с фактической цепочкой
original W → original P → retained CompilerCommand. Вход W без аргументов не
принимает READY observation как разрешение; private same-thread phases и свежие
проверки original READY/capture/liveness/context/deadline сохраняются до native
границы. Generic Resume отклоняет producer-mode команды. Shared native body
сохраняет counts 1→2→1 и sticky attempts, повторяет root/Job проверку; после
последней W-проверки до native Resume нет нового held-input обхода. Отдельный
unconditional first-continuation blocker сохранён. Независимый source review завершён;
пять source/BCL-наборов 120/190/100/84/80 прошли PS5/PS7 с первой попытки
(574 на оболочку). Межпроцессная liveness не становится атомарной:
смерть W или ещё не прочитанные pipe bytes после последней проверки остаются
qualification gap. C#/native/production не запускаются, цель R0–R9 и разрешения прежние.
[Результаты первого continuation и следующие зависимости](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/producer-first-continuation-20260923/RESULTS.md).

Предыдущий code-only этап: реальные L-side фабрики запуска P/W переведены на единый
удерживаемый StartupPreloadOwner. Прежние public raw Prepare заменены внутренними
фабриками: executable, отдельные lease sets, environment, cwd и context выводятся
из одного private independently selected descriptor. Selector остаётся null,
а перед preload Open стоит отдельный unconditional qualification blocker.
Проверка same-owner встроена в фактические pre-create boundaries; сохранены исходные
P/W containment/dispose и освобождение входов только после них. Добавлены атомарно
удерживаемые original-wrapper references для явного recovery после прерванной подготовки.
Независимый source-review завершён; четыре направленных source/BCL-набора
120/190/100/84 прошли PS5/PS7 с первой попытки (494 на оболочку).
Это не квалификация полного CLR/native resolution или уже загруженного L/bootstrap.
Generic CompilerCommand API остаётся unqualified; данный owner закрывает dedicated
P/W wrapper path, а не произвольный код в доверенном host. Production/полная R0–R9
и разрешения не менялись; C#/native не исполнялись.
[Результаты этапа и следующие зависимости](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/startup-preload-owner-20260923/RESULTS.md).

Предыдущий code-only этап: фактический P-side coordinator удерживает unopened input owner,
выведенный только из исходного admitted context. Отдельный W admission ACK подтверждает
наблюдение P HELLO, но не разрешает Open и не доказывает непрерывный надзор W. Strict ABI
обновлена до P entry/control v3 и W launch/watch v5; STOP закрывает новый control admission.
Legacy owner теперь также запрещает reserved COM/LPT с superscript 1–3. Независимый
source-review завершён; семь наборов 120/78/162/190/100/168/64 прошли PS5/PS7 с первой
попытки (882 на оболочку). C# не компилировался и не исполнялся. Trust selector deny-all
и отдельный unconditional payload-Open blocker сохранены. Production, полная R0–R9
и все границы разрешений не меняются.
[Результаты и незакрытые зависимости](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/producer-input-coordinator-20260923/RESULTS.md).

Предыдущий code-only этап: строгая metadata schema и вывод inventory из фактических
context bytes подключены внутри retained P/W capture worker. Семантическая проверка
отделена от независимого допуска: trust selector пока deny-all, поэтому HELLO/READY,
payload Open, P Resume и compiler actions не разрешены. Исправлены найденные при review
Int64-boundary и reserved-path проверки; независимые source review завершены.
Четыре source-набора 60/66/148/168 прошли PS5/PS7 с первой попытки (442 на оболочку).
C# не компилировался и не исполнялся; production/разрешения не менялись, full R0–R9
не принят. Далее — actual P-side unopened owner/coordinator, независимый trust/preload
scope и полный Job/root/terminal-протокол, затем отдельно согласуемая квалификация.
[Результаты и границы этапа](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/compiled-context-preflight-20260923/RESULTS.md).

Предыдущий code-only этап: доставка exact context bytes связана с реальными P/W launch
и entrypoint-исходниками. Retained unnamed mapping имеет отдельные read-only копии;
выдача запрещена до подтверждённых Unmap writable view и Close full-access handle.
P entry/control v2 и W v4 принимают строгие length/hash/handle, а HELLO/READY связывают
их с прежними identity/nonce/ticks. Capture выполняется фоново; W main не ждёт его
и сохраняет original-P watch/STOP. Независимый source review выполнен; шесть наборов
114/66/148/52/190/100 прошли PS5/PS7 с первой попытки (670 на каждую оболочку).
После перерыва завершены оставшиеся проверки, неизменённые прогоны сохранены. C# не компилировался,
P/W и native не запускались. Передача байтов не доказывает approved provenance/schema,
не открывает payload и не разрешает Resume; full R0–R9 и границы разрешений сохранены.
[Результаты и оставшиеся зависимости](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/producer-context-delivery-20260915/RESULTS.md).

Предыдущий code-only этап: общая metadata/preflight-проверка выделена перед подготовкой
owner; сама фабрика теперь выводит точный inventory из raw profile/plan/manifest,
не принимает произвольные SpecRecords и проверяет manifest aliases. Полная проверка
фактически удерживаемых байтов после Open сохранена. Source/pure-наборы 148/79/114
прошли PS5/PS7 с первой попытки; независимый source review выполнен.
Это не готовый P-side coordinator: standalone P ещё не получает approved raw context,
не исполняет этот PowerShell adapter и не имеет полномочия открыть payload.
[Результаты этапа и следующий реальный integration gap](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/build-input-preflight-20260915/RESULTS.md).

Предыдущий code-only этап: добавлен отдельный private P↔W transport и строгие HELLO
entrypoints. Трубы не используют stdout/stderr; исходные native-исходы удерживаются,
копии L снимаются после подтверждённой передачи каждому процессу. P Resume и Job/root
ACK пока отсутствуют; source-наборы 190/128/50/98 прошли PS5/PS7, независимый source
review завершён. Это не C#/native-квалификация. Найден и исправлен порядок дальнейшей
интеграции: payload нельзя открывать в L под видом защиты W, способного остановить только P.
Кандидатные входы должны открываться внутри P после реального вооружения W; доверенная
предзагрузка binaries — отдельная внешне квалифицированная граница.
[Результаты этапа и незакрытые требования](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/producer-control-transport-20260915/RESULTS.md).

Продолжение после проверки формулировки цели: полная цель в карточке уже active и
соответствует R0–R9; менять критерии завершения или расширять разрешения не требуется.
Передача единого срока запуска P/W без перезапуска часов при рождении W добавлена
и независимо проверена на уровне исходников; наборы 140/100/98 прошли PS5/PS7.
Это не общий срок всей сборки: прежнее открытие входов и независимый approved context
ещё требуют интеграции. После этого — compiler Job/root ACK и полный terminal-протокол;
затем отдельная квалификация актуальной ревизии. Нативные/production-окна не открыты.
[Результаты и оставшиеся зависимости](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/build-startup-deadline-20260915/RESULTS.md).

Последний code-only checkpoint, 2026-09-15: исходники отдельного running-from-birth W,
его owner-watch entrypoint и приёма private READY связаны с исходным P. W не получает
P Job; копирование stop-capability сериализовано с P containment, входы W удерживаются
отдельно. Исправлены повторное освобождение при unknown native outcome и аварийное
дочитывание capture. Source-наборы 90/98/64 прошли PS5/PS7; C# и native не исполнялись.
После W creation return наследование временной P-copy снимается; это не заменяет
отдельную квалификацию L без посторонних concurrent inherit-all запусков.
READY пока не разрешает P Resume; entrypoint всегда завершает этап без принятия сборки.
Общий original deadline, compiler Job/root ACK и полный terminal ещё не реализованы.
[Результаты и матрица следующей квалификации](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/guardian-owner-watch-20260915/RESULTS.md).

Предыдущий code-only checkpoint, 2026-09-15: добавлена удерживаемая фабрика отдельного suspended
producer, использующая существующий birth Job, и внутреннее право остановки только
исходного нового процесса. Shared launcher не является terminate-target. Публичного
Resume или приёма PID/чужого handle нет; работающий W ещё не подключён. Независимый source
review выполнен; новые 54/54 и связанные 92/92 прошли PS5/PS7. Первые два отказа setup
нового теста сохранены; target-код для их исправления не менялся. C# не компилировался,
native-код не исполнялся и не квалифицирован. [Результаты этапа](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/disposable-producer-startup-20260915/RESULTS.md).

Предыдущий code-only checkpoint, 2026-09-15: добавлено владение реальными входными файлами
отдельной сборки и его связь с compiler-bootstrap. Независимая source-проверка выполнена;
92/92 source/BCL cases прошли PS5/PS7. Два первых отказа ошибочного тестового селектора
сохранены; селектор уточнён без изменения защитного кода. Полная цель в карточке корректна
и active; обновлена последовательность задач, не критерии или разрешения. C# не
компилировался и не исполнялся. Полный независимый watchdog, изоляция записи output и
actual coordinator остаются обязательными. [Результаты](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/build-input-ownership-20260915/RESULTS.md).

Предыдущий code-only checkpoint, 2026-09-15: исправлен suspend-count порядок во всех четырёх
проверках runtime и обоих pre-resume abort; неверный prior count не достигает ResumeThread.
В NativeChild отказ проверки сохраняется между попытками; фактическая попытка Resume
фиксируется до native-вызова и не может повторно перезапустить часы. Независимый source
review выполнен; итоговые source-наборы 64/72/134/48 прошли PS5/PS7 (8 запусков exit0).
Это не компиляция, native-квалификация или приёмка полного протокола; прежние runtime
concurrency/constructor/cleanup ограничения не объявлены решёнными. Pins/WIP и production
не менялись. [Результаты](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/suspend-order-20260915/RESULTS.md).
Далее — реальный build coordinator, независимый watchdog и полный input/output scope,
затем отдельно согласуемая bootstrap qualification, versioned R1/G/P/T и вся R0–R9.

Предыдущий code-only checkpoint, 2026-09-15: добавлены исходник отдельного compiler-bootstrap
и BCL-only загрузчик его точных заранее закреплённых байтов. Новый source/pure набор прошёл
79/79 в PS5/PS7; C# не компилировался, загрузчик и native lifecycle не исполнялись.
Это ещё не actual producer, квалификация bootstrap или R1/G/P/T-интеграция.
[Результаты и ограничения](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/build-bootstrap-20260915/RESULTS.md).
Независимый обзор исправил в новом runner слишком позднюю проверку suspend count и
невозможность подтвердить cleanup известного созданного root до success-identity setup.
Аналогичный прежний порядок SuspendThread/ResumeThread в runtime5d38 остаётся отдельным
обязательным code-only исправлением перед дальнейшей приёмкой; native R2 не повторять.
Далее — это исправление, настоящий coordinator/контроль внешнего watchdog и input/output
scope, затем отдельно согласуемая bootstrap qualification и неизменённая полная R0–R9.
Бюджеты 60/30 нового runner — границы проверок при вызовах, не автономный STOP и не
изменение прежних 15/25-секундных порогов. G/P/T/v3 blockers и production не изменены.

Предыдущий code-only checkpoint, 2026-09-15: новый v2 контракт разделяет фактическую
сборку в отдельном build-worktree/private output и логические файлы будущей R1-публикации.
Проверяются исходный manifest, independently pinned profile/plan, все заявленные входные
байты, receipt, stdout/stderr и равенство build/sealed DLL snapshots; коллизии путей запрещены.
Это чистые проверки соответствия данных, не выполнение компилятора, не metadata/native
qualification и не полномочие импорта. [Результаты и границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/runtime-r1-integration-20260915/RESULTS.md).
Старый v1 остаётся неавторизующим сравнением, не literal compiler recipe для нового pipeline.
Далее реальный bounded build producer, независимая metadata/profile qualification и
versioned R1/оба anchor; затем BCL-only pre-import bootstrap без самоодобрения через target
runtime, exact-byte G/P/T import и остальная полная цепочка. Все v3 seals, WIP/pins/no-cache
и разрешения сохранены; production не менялся, полная цель active, R0–R9 не приняты.

Предыдущий code-only checkpoint, 2026-09-15: реальный parent call site сохраняет исходный
Transition-объект до native Start; session C связан с этим exact suspended T, restricted
handle включён в inheritance и полный argv. Неопределённые результаты creation/duplication
не разрешают native cleanup; повтор частичной очистки не использует уже закрытые handles.
Ранний parent-mode guard T остаётся заблокирован до квалифицированного runtime import,
до файловых reservation/fence/Docker. Source/managed проверки и compile-only — не native
квалификация. [Результаты и границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/transition-command-admission-20260915/RESULTS.md).
Далее versioned R1/DLL build provenance, same-held-byte import, фактический T command-wrapper
и отдельное G start-дерево; затем полный terminal/receiver/causal STOP и takeover.
WIP/pins/no-cache, production и разрешения сохранены. Полная цель active, R0–R9 не приняты.

Предыдущий code-only checkpoint, 2026-09-15: добавлены удерживаемые session C/per-command D
API и чистая проверка привязки runtime к исходным байтам/сборке. Новые source/pure наборы
76/62/146 и девять прежних наборов прошли обе оболочки — 24 успешных итоговых запуска.
Runtime только скомпилирован; native lifecycle не исполнялся. Независимый обзор выявил
ETS-подмену скалярных/массивных членов: исправление и все промежуточные отказы сохранены.
[Результаты, точные границы и следующий шаг](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/command-session-20260915/RESULTS.md).
Реальные C→T export/import и wrapper, отдельное G start-дерево, versioned R1/DLL receipt,
полный terminal/receiver и takeover ещё не подключены. Далее code-only интеграция этих
зависимостей, без снятия WIP/pins/no-cache и без нового native/production-окна.
Полная цель active, описание и все R0–R9 критерии/разрешения неизменны.

Предыдущий code-only checkpoint, 2026-09-15: NativeChild использует обязательный
creation-time JOB_LIST вместо создания процесса с последующим Assign. Новый source-only
набор прошёл 56/56 в PS5.1 и PS7; восемь связанных прежних наборов также прошли обе оболочки
(всего 18 успешных запусков девяти наборов). Это не повтор native Sandbox 56/56 и не
квалификация Windows lifecycle. [Результаты и ограничения](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/command-birth-20260915/RESULTS.md).
Session C/per-command D, владение start-деревом gate, runtime-import до первой команды T,
полный terminal/receiver и takeover остаются открытыми. Далее code-only интеграция этих
границ; унаследованные Dispose/Wait ограничения NativeChild не считать решёнными.
Production, pins/WIP/no-cache и разрешения сохранены; полная цель active, R0–R9 не приняты.

Предыдущий code-only checkpoint, 2026-09-15: terminal CONTROL half-close реализован в
runtime и success-only watchdog ветке; unresolved start больше не принимается по одному
materialized/stopped snapshot без exact STOP и свежей проверки. Новые managed/source/
wiring/STOP наборы прошли 12/74/30/63 в каждой оболочке; все 24 набора — 48 exit0 запусков.
[Результаты и обязательные оставшиеся границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-control-drain-20260915/RESULTS.md).
Реальные OS pipes/native lifecycle не исполнялись. Gate terminal seam и pre-READY failure
drain ещё не связаны; command ownership обоих участников и takeover/fence порядок остаются
обязательными. Далее code-only атомарное владение деревьями команд и полный terminal/receiver
путь. Pins/WIP/no-cache, production и разрешения сохранены; полная цель active, R0–R9 не приняты.

Предыдущий code-only checkpoint, 2026-09-15: ранний private CONTROL pilot связан между
gate и watchdog; actual pure request/response roundtrip проверен. Новые wire/native-source/
client/responder/interop наборы прошли 41/70/44/71/31 в каждой оболочке; неавторизующее
root-death observation — 48/48. Все 20 ограниченных наборов прошли обе оболочки; runtime
только compile-only. [Результаты и оставшиеся границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-transition-control-20260915/RESULTS.md).
Предел 1000ms, native transport, graceful EOF и causal descendant containment ещё не
квалифицированы. Gate после пилота безусловно блокируется до legacy heartbeat/fence/start;
все pins/entry/no-cache и разрешения сохранены. Далее code-only closure/takeover и полный
terminal/boundary-протокол. Production не менялся; полная цель active, R0–R9 не приняты.

Предыдущий code-only checkpoint, 2026-09-15: ранний gate intent связан с собственным
independent Transition launch/capture parent watchdog; wire/native-source/parent-launch
наборы прошли 38/60/67 в каждой оболочке. Прежний continuation selector уточнён до исходного
класса без удаления старых 29 случаев; добавлены 4 controls, итог 33/33. Все прежние регрессии
также прошли. Runtime только compile-only; native lifecycle и полный протокол не приняты.
[Результаты, ошибки и следующие integration gaps](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-transition-launch-20260915/RESULTS.md).
После раннего ACK gate безусловно заблокирован до legacy lease/heartbeat/start-fence/start.
Resumed-guard causal containment/nonzero terminal ABI ещё не квалифицированы; независимый
guard, HBA и fences не освобождаются на основании exit-only или JSON. Далее code-only
fresh-live/terminal/takeover связь, затем остальная full-chain приёмка. Pins/entry/no-cache
blockers, production и границы разрешений сохранены; полная цель active, R0–R9 не приняты.

Статус карточки исправлен 2026-09-14 в 21:25 UTC по прямому запросу пользователя:
полный текст из `CURRENT_PAPER_RELEASE_OBJECTIVE.txt` установлен вместо ошибочного
`resume`, статус `active` подтверждён отдельным чтением `get_goal`. Использован штатный
documented app-server `thread/goal/set`; база приложения вручную не редактировалась,
цель не объявлялась завершённой и не очищалась. История и прежние счётчики сохранены.
Доказательства: `.stratus-cache/goal-resume-20260914/before.json`, `after.json`, `attempts.json`.
Прежние записи о `resume / blocked` ниже относятся к истории. Автоматическое продолжение
не расширяет перечисленные границы разрешений и не разрешает production/LIVE.

Принятый native checkpoint, 19:42 UTC: в исправленном отдельном Sandbox-пакете R2 обе замороженные
native-матрицы прошли 56/56 (PS5.1 и PS7.6.5), evidence независимо проверено, гость закрыт,
production и host policies не изменены. Это закрывает только native prerequisite; полный
parent/bootstrap-протокол, актуальные R0/R1 и остальная release-цепочка ещё не приняты.
Ранние статусы 45/56 ниже сохранены как история, а не текущий результат этой гостевой проверки.

Текущий code-only checkpoint, 20:56 UTC: watchdog обнаруживает потерю mutation fence до
продолжения heartbeat и удерживает восстановленную блокировку для containment. Новый
отдельный managed-FS/AST набор прошёл 10/10 в PS5.1 и PS7; независимая проверка выполнена.
[Результат и оставшиеся integration gaps](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-fence-loss-20260914/RESULTS.md).
Watchdog изменён относительно замороженного R2; прежний pin gate не заменён, entry blockers
сохранены. Это ограниченное исправление, не приёмка полного протокола или новой R0/R1.

Следующий code-only checkpoint, 21:10 UTC: отдельный чистый validator структуры и порядка
записей PostgreSQL start-fence/attempt/completion прошёл 70/70 в обеих оболочках и независимый
обзор. Он не подключён к watchdog/gate/cache и не доказывает происхождение данных или
проверку их исходных байтов; все разрешения в результате false.
[Точные границы, результаты и оставшаяся работа](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-transition-lineage-20260914/RESULTS.md).
Следующий code-only checkpoint, 21:48 UTC: gate больше не принимает внешние lease/ready/fence
как полномочия; собственный bootstrap, arming и проверка удерживаемых ресурсов связаны в коде
за безусловными блокировками входа. AST-проверки 17/17 и managed-модели lease/cleanup 63/63
прошли в PS5.1 и PS7; точные исходники независимо проверены.
[Доказательства и ограничения](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-bootstrap-integration-20260914/RESULTS.md).
Это не native-проверка или приёмка полного протокола. Ближайшая работа — same-process
продолжение после Transition с удерживаемой защитой и явным terminal/containment-путём;
пойманная ошибка cleanup и возвращённый JSON этого не обеспечивают. Затем полная связь
parent/transition, владение observer и непрерывная защита после нагрузки. Старый integration
harness требует адаптации к собственному bootstrap без ослабления проверок; полный CI не принят.

Следующий code-only checkpoint, 22:09 UTC: gate передаёт внутренний результат Transition
своему продолжению без выдачи промежуточного JSON наружу, публикует связанную с удерживаемыми
доказательствами регистрацию и требует точный ACK от собственного watchdog. Ошибка cleanup
теперь прерывает возврат до освобождения ресурсов, сохраняя исходную ошибку; это ещё не
реализация непрерывного владения после раскрутки стека. Для native runtime добавлено только
наблюдение точного нормального выхода watchdog и обоих EOF, без вызова из gate.
AST-наборы 17/17 и 29/29, managed-модели 94/94 прошли в обеих оболочках; native-код только
скомпилирован, без вызова его членов. Независимая проверка выполнена; два пробела новых
статических тестов закрыты дополнительными отрицательными случаями.
[Результат и точные границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-continuation-integration-20260914/RESULTS.md).
Ни один entry blocker или pin не снят. Runtime и watchdog отличаются от замороженного R2.
Ближайшая работа остаётся code-only: полная причинная связь parent/Transition, единый
владелец provision/API/load и observer, отдельный cooperative terminal STOP с проверкой
доказательств остановки. Старый standalone-путь и снятие защиты после нагрузки явно
заблокированы в runbook. Полный протокол, актуальная native-матрица, полный CI и R0–R9
не приняты; новых production/Sandbox-разрешений этот checkpoint не предоставляет.

Следующий code-only checkpoint, 22:42 UTC: receiver удерживает регистрацию и десять связанных
документов, проверяет исходные байты и связывает их с собственной R1/parent-привязкой.
Новые ограниченные наборы прошли 53/53 (managed-FS/AST) и 66/66 (чистая модель) в обеих
оболочках; набор fence/heartbeat расширен до 12/12, прежние проверки сохранены. Проверка
внутренних доказательств repair пока выборочная; происхождение native capture и завершение
containment при внешней hardlink-подмене ещё не доказаны. ACK/cache остаются безусловно
заблокированными. [Доказательства и точные границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-transition-evidence-20260914/RESULTS.md).
Независимый обзор выполнен. Полная цель остаётся активной; production и Docker-сервисы не
изменены, ни один pin или entry blocker не снят. Далее закрыть отмеченные пробелы receiver/
containment и единый downstream/observer/terminal-протокол, затем квалифицировать актуальную
чистую ревизию; этот checkpoint не заменяет полную приёмку или новые разрешения.

Следующий code-only checkpoint, 23:08 UTC: receiver захватывает Transition-fence эксклюзивно
до остальных доказательств и удерживает тот же handle; прежний shared→exclusive разрыв убран.
В аварийной ветке плохие bytes/metadata не повышают полномочия и остаются диагностикой только
при остановленном точном owner и сохранённом собственном exclusive-владении; потеря/подмена
handle строго отклоняется. Проверка repair расширена до полного доступного durable-графа
снимков, вложенных хешей и событий. Наборы 73/73 и 151/151 прошли в обеих оболочках, как и
прежние 17/94/29/12/70; независимый source-review выполнен.
[Доказательства и ограничения](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-fence-continuity-repair-20260914/RESULTS.md).
Первичная подмена пути, native capture/владение и полный containment этим не квалифицированы.
ACK/cache, pins и entry blockers сохранены. Далее — независимое происхождение successor capture,
полная receiver-проверка, raw R1 inputs и единый downstream/observer/cooperative STOP. Полная
цель активна; R0–R9 не приняты, production и разрешения не изменены.

Следующий code-only checkpoint, 23:43 UTC: подготовлен собственный observer launch/capture
контур с fixed argv/nonce, READY/COMPLETE wire, held full-output hash и completion receipt.
ACK дополнительно проверяет live/clean capability до публикации и после readback; исправлена
parent-mode нормализация фактического PostgreSQL timestamp. Наборы 46/42/14/115 и прежние
17/94/29/12/70/151 прошли в обеих оболочках; evidence-набор расширен 73→75 без удаления старых
случаев. Сохранены ранние ошибки нового каркаса и два отказа старого AST layout-теста.
[Итог, история и точные границы](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/parent-owned-observer-20260914/RESULTS.md).
Runtime только скомпилирован; native launch/capture, полный raw sample/composite и вся цепочка
не квалифицированы. Gate-side sender/observer intent ещё не связан; API/provision private cache,
Transition capture и cooperative STOP остаются открыты. Pins/entry/no-ACK/no-cache barriers
сохранены. Следующий code-only шаг — независимый ранний Transition launch/capture с поздним
exclusive fence capture; обычный observer Job не подходит safety-guard lifetime автоматически.
Production/сервисы и разрешения неизменны, полная цель active, R0–R9 не приняты.

## Актуальное задание для продолжения

Готовый текст для карточки: [CURRENT_PAPER_RELEASE_OBJECTIVE.txt](C:/Users/Administrator/Documents/weatherpoly/docs/CURRENT_PAPER_RELEASE_OBJECTIVE.txt).
Обновление этих файлов само по себе не изменяет objective или статус карточки Codex.

Продолжать полную release-цель STRATUS для Polymarket PAPER по цепочке R0–R9 и критериям
ниже. Принятый native prerequisite не означает завершение этой цели или разрешение
production-перехода.

Приоритетная очередь на 2026-09-27 (уточняет историческую последовательность ниже):

Конечная цель и текст карточки остаются прежними: полная приёмка R0–R9 и закрытие
исходного инцидента. Обновляется очередь работ, не критерии завершения и не полномочия.
Новые 11 source-only попыток R4 завершены: 1234 cases, без повторения семи ранее
успешных PS5 suites. Независимый аудит подтвердил объединённый source checkpoint:
2096 cases, по 1048 на PS5/PS7, из отдельных окон R2/R3/R4, не единый новый прогон.
Итог и ограничения: [FINAL-STATUS.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/guardian-pipe-source-r4-20260927/FINAL-STATUS.md),
независимый [AUDIT.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/guardian-pipe-source-r4-20260927/AUDIT.md).
Это не native-проверка или приёмка полного протокола. Конкретный профиль следующей
интеграции выбран и независимо проверен как code-only design:
[PROFILE-DECISION.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-next-integration-20260927/PROFILE-DECISION.md),
[PROFILE-REVIEW.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-next-integration-20260927/PROFILE-REVIEW.md).
Связанный первый source-срез S→L теперь написан: пять новых файлов и изменения
четырёх существующих связывают retained security/session owner, original birth,
bounded S/L wire и actual P/W capability ACK до существующего Resume. Ограниченные
независимые source reviews сохранены; исправлены найденные гонки ACK/source-slot
retirement и ошибки timeout/first-fault publication. Точные pins, объём проверки и
оставшиеся пробелы: [STATUS.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-session-source-20260927/STATUS.md).
Это частичный непроверенный исполнением source checkpoint, не завершённая первая
интеграция, не установленный writer backend и не full/native acceptance. Прежний
R4 source PASS не квалифицирует изменённые bytes. Следующий post-W source-срез
теперь реализован в семи изменённых файлах: original normal-terminal origin,
немедленное v2-уведомление до retirement/capture и внешний 30000-ms intersection.
Ограниченные независимые reviews не нашли новых блокирующих дефектов в этом срезе:
[STATUS.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/terminal-postw-source-20260927/STATUS.md).
Это не тестовый PASS или native/full acceptance. Следующий source-срез теперь
связывает original S-owned namespace, held ancestry, одноразовое защищённое
создание каталога и typed session policy. Ограниченные независимые source reviews
и точные pins сохранены в
[STATUS.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/namespace-source-20260927/STATUS.md).
Это pre-input acquisition source, не установленный backend или допуск к запуску.
Следующий original fresh-R installer теперь также реализован только в исходниках:
все три private baselines проверяются до первой записи, original management handles
удерживаются эксклюзивно, separate install/readback/final outcomes сохраняются.
Независимые source reviews, ограничения выбранной local-NTFS модели и точные pins:
[STATUS.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/namespace-install-source-20260927/STATUS.md).
Реальные sharing/no-child-propagation и native compatibility не квалифицированы;
соответствующие unconditional gates закрыты, общие grants и rollback неизвестных
операций не добавлены. Следующий embedded-input source-срез теперь реализован:
exact CREATE_NEW встроенных документов на их явно объявленных direct-child путях,
known write/flush/native writer close и retained read-only same-object byte pin
связаны с отдельным original S pre-exposure окном. Независимая проверка выявила и
устранила пересечение с зарезервированным stdout; фактические scope/pins/ограничения:
[STATUS.md](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/namespace-input-source-20260927/STATUS.md).
Это не external-input admission, полная поддержка всех metadata layouts или
native qualification. Внешние входы не переносились; неподдержанные nested/payload
layouts отклоняются до записи без изменения схемы. Следующее code-only действие —
exact external-input/loader admission и недостающий provisioning contract выбранной
раскладки, canonical remote observation и actual shared P/L writer bindings,
а также original lifetime protocol для known successful R-grant removal и
management/input-read-pin resource retirement. После них — held image/entry closure
и реальный sealing/accepted handoff. Неизвестные partial grants и stale R не считать
очищенными или пригодными к повторному использованию.
Native/test/production gates и разрешения не изменены.
Повторять неизменённые source suites либо
замороженные native 56/56 или начинать новый общий поиск механизмов вместо реализации
не требуется.

Завершены source-only compiler-in-P checkpoint и следующий actual Job transfer/ACK
с transfer-aware STOP checkpoint (последний — 1286/1286 на каждой оболочке).
Не повторять неизменённые прогоны вместо следующей реальной интеграции.
Независимый аудит и окончательный индекс этого checkpoint теперь сохранены.
Следующий compiler birth/root handoff теперь реализован и независимо проверен
только как source-этап: 1408/1408 на каждой оболочке; полный terminal остаётся закрыт.
[Карта следующей интеграции](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/compiler-root-handoff-20260925/TERMINAL-HANDOFF.md)
сохраняется как исторический план, а не приёмка текущих исходников или новое
разрешение. В `.stratus-cache/terminal-protocol-20260926/DESIGN-REVIEW.md` сохранён
независимый design review; окончательная source acceptance и test-safety freeze
этого terminal-этапа пока отсутствуют.

1. Завершить и независимо проверить уже написанную success/failure terminal-цепочку:
   compiler/collector closure → typed registration release и actual input recapture
   → P/W ACK/half-close и оба private EOF → physical P/W exit и outer EOF.
   Shared wire/commitment DTO уже отделены от прямой P-only compiler/artifact
   authority; актуальные producer/consumer ABI согласованы (P v6, W v8).
   Статическая карта фактических source dependencies сохранена в
   `.stratus-cache/terminal-protocol-20260926/SOURCE-CLOSURES.md` (W11/L26/P27).
   Это не compiler result, final freeze или готовая квалифицированная сборка.
   Обновить соответствующие тесты после review, сохранив прежние cases и bounds.
   Bounded перечень устаревших selectors и отдельных coverage gaps сохранён в
   `.stratus-cache/terminal-protocol-20260926/TEST-UPDATE-INVENTORY.md`;
   он не является test-safety разрешением или итоговой матрицей отказов.
   P не ждёт W death; W не ждёт P death перед provisional ACK.
   Сохранить phase-aware drain, read-only normal Job/root closure отдельно от STOP,
   source-slot pinning, single-use outcomes, все bounds и unconditional gates.
2. Завершить независимую проверку новой actual held-output/L coordinator цепочки
   вместе со всем parent/bootstrap-протоколом и матрицей отказов текущей ревизии.
   Новый coordinator уже связан с original W normal-closure hook и original P
   observation; output owner удерживается до preload/P birth, фактические bytes
   читаются через retained handles после physical P/W exit и outer EOF.
   Это source implementation, не acceptance: независимый shared P/L writer
   mechanism, pre-P/post-W external L supervision, success sealing и передача
   принятого результата дальше ещё не реализованы/не квалифицированы.
   P-side artifact transport и L capture с false flags их не заменяют.
   Завершить проверку замены managed Dispose-success inference на retained exact
   native-close outcomes для original input directory/file/stream slots и compiler
   executable/stdio ends (включая ранние client-end closes). Ограниченная реализация
   Inputs/Bootstrap и её source-review уже сохранены. Directed coverage и
   независимая проверка её переноса завершены: ownership содержит 96 source pairs
   и 2 BCL cases, birth-job — 31 pair. После нового независимого точного
   test-safety freeze они прошли 194/194 + 62/62 на каждой PS5/PS7, всего 512;
   все 17 проверок целостности и ресурсные проверки до/после совпали. Доказательства:
   `.stratus-cache/native-resource-source-20260926/RESULTS.md`. Это source/AST/BCL
   этап, не C# compilation/native или приёмка полного протокола. Четыре разрешённые
   попытки израсходованы, повторять неизменённый этап нельзя. Независимый аудит
   сохранённых результатов завершён без расхождений; его точные ограничения
   сохранены в AUDIT.md рядом с RESULTS.md. Следующим отдельным code-only этапом
   выделено L/Guardian resource ownership; writer/lifetime/sealing барьеры остаются.
   Для L/Guardian сохранены NEXT-L-RESOURCE-SCOPE.md, независимый
   DESIGN-L-GUARDIAN-PIPES.md и prospective L-FAILURE-MATRIX.md в
   `.stratus-cache/native-resource-ownership-20260926`. Реализация ограничена
   GuardianLaunch.cs; code-only интеграция сохранена и независимо проверена в
   ограниченном source scope: 92695 bytes, SHA256
   `3cf228b83924eec497245272e94466b5cd8ebffd11e96abb7a603fcc286d5f28`.
   REVIEW-L-ACQUISITION.md и REVIEW-L-COLLECTORS.md не нашли блокирующих дефектов
   в своих частях; actual caller-chain отдельно проверена в REVIEW-L-CALLERS.md.
   Это не компиляция, native-квалификация или приёмка полного протокола.
   Исходная направленная suite GuardianPipeOwnership: 68 pairs / 136 заявленных
   cases, 57036 bytes, SHA256
   `db4bb13040cae6d7b7a6524fe2f4af62747496e847ea145d63a3ce921e5461d1`.
   Независимый REVIEW-L-DIRECTED-COVERAGE.md сохранил три пробела первоначальных
   58 pairs и проверил их устранение десятью добавленными pairs: actual caller
   links и точные outcome-helper formulas. После отдельного точного test-safety
   freeze первый PS5 прогон остановился на неоднозначном positive clock selector
   (child exit 1, outer exit 22), без итогового JSON/PASS. Все 47 guards совпали
   до/после; ресурсные ограничения соблюдены. Остальные 17 попыток не запускались, окно
   `.stratus-cache/guardian-pipe-source-20260927` закрыто и израсходовано.
   ROOTCAUSE.md в этом каталоге отделяет дефект тестового selector от runtime;
   исходный failed test сохранён в FAILED-TEST-SNAPSHOT.json. Не повторять
   попытку и не запускать следующие suites по старому решению.
   Четыре старых predicate rows в трёх suites перенесены без удаления прежних
   negatives; восемь старых suites сохраняют все 456 IDs / 912 cases. Отдельно
   ужесточены их read/token helpers: bounded read до выделения буфера, pre-Add
   token cap, regex timeout. REVIEW-L-MIGRATION.md и REVIEW-L-HARNESS-BOUNDS.md
   фиксируют независимые ограниченные проверки, не разрешение исполнения.
   Полная независимая проверка исполняемой поверхности всех девяти suites
   завершена для закреплённых в этом закрытом окне bytes. Отдельная code-only
   правка selector выполнена после аудита: текущий test 57132 bytes, SHA256
   `6cc5c1ab5cc5804d75ee5532e18707a2031b90738138f3af495a20ef4093ede4`.
   Оба runtime clock guards, все IDs и существующий P-first negative сохранены;
   независимая проверка точной однострочной delta завершена без найденного
   ослабления в `.stratus-cache/guardian-pipe-selector-fix-20260927/REVIEW.md`.
   После нового независимого точного R2 freeze исправленный test прошёл 136/136
   на PS5, BirthJobPreparation — 62/62. Третий прогон CompilerJobTransfer
   завершился child 1 / outer 22 на проверке числа строк до case loop; итогового
   suite JSON/case count у него нет. Все 55 guards совпали до/после трёх запусков
   (330 сравнений), ресурсные проверки пройдены. Оставшиеся 15 попыток, включая
   все PS7, не запускались; R2 окно закрыто и израсходовано. Доказательства:
   `.stratus-cache/guardian-pipe-source-r2-20260927/FINAL-STATUS.md`.
   Независимый аудит R2 завершён: 198 полных source cases, 330/330 guards и
   6/6 resource admissions подтверждены; ограничение failure-validation и
   известные child 1 / outer 22 сохранены отдельно, без перезаписи raw evidence.
   Отдельная code-only коррекция singleton row append в Job и аналогичном ещё
   не запущенном Root выполнена: по одной unary comma, без изменения строгих
   количеств, IDs/predicates/negatives. Текущие Job 69177 bytes / SHA256
   `4afa5a30610ab216cabcc924f4feecae37ab8a521e6eff0f2739bf3d4c92b1d7`,
   Root 78186 bytes / SHA256
   `f697e686f19dc10609a42cc43d2feb0fb0246882afb1e07264cc6f7889058d99`.
   Независимая exact-delta проверка завершена без найденного ослабления в
   `.stratus-cache/compiler-transfer-row-shape-fix-20260927/REVIEW-ROOTCAUSE.md`;
   их последующие R3 результаты указаны ниже. Исходные bytes сохранены в R2
   TRANSFER-TESTS-SNAPSHOT.json; failed validation не переписывать в PASS.
   Новое точное R3 окно включало только 7 оставшихся PS5 и 9 PS7 попыток.
   Первые пять PS5 suites завершены: Job 110, Root 184, Context 100,
   Disposable 190, Continuation 80 — всего 664 полных source cases.
   Шестой StartupPreload остановился на устаревшем contiguous selector:
   child 1 / outer 22, без итогового suite JSON/case count. Десять следующих
   попыток, включая все PS7, не запускались; R3 закрыто и израсходовано.
   Сохранены `.stratus-cache/guardian-pipe-source-r3-20260927/FINAL-STATUS.md`,
   исходные journals/validations, FAILURE-READOUT.json и exact failed-test
   snapshot. Независимый аудит завершён: подтверждены 664 cases, 792/792
   historical guards и 12/12 resource admissions. 1898 planned cases не считать
   выполненными. В R3 Birth/AST suite не выполнялась; это лишь часть плана и
   отдельной исторической R2 evidence. Успешные неизменённые PS5 198 из R2 и 664
   из R3 не повторять без новой проверяемой причины; это ещё не полная матрица.
   После независимого raw diagnosis выполнена отдельная code-only правка
   StartupPreload: в positive Has добавлен существующий actual вызов
   `AssertOriginalStdioForBirth();`, исходный negative последней SuccessBoundary
   сохранён. Текущий test 38040 bytes / SHA256
   `c4dfadd7e6c93549f128c1506444bbc990c159504f0b7a26bbd49d8946f12a8f`;
   whole-text delta — только 31-byte insertion; 42 IDs / 84 cases неизменны.
   Runtime не менялся; до отдельного R4 окна исправленный test не запускался. Независимый exact-delta
   review завершён в R3 ROOTCAUSE-PRELOAD.md без найденного ослабления.
   После нового независимого точного R4 test-safety freeze прошли исправленный
   PS5 Preload 84, PS5 Terminal 102 и все девять PS7 suites 1048: всего 11 новых
   попыток / 1234 завершённых source cases. Окно закрыто и израсходовано, новых
   запусков по этому решению нет. Все child/outer exits равны 0; совпали 1848/1848
   before/after guards, пройдены 22/22 resource admissions. Исходные journals,
   validations и RESULTS.md сохранены в
   `.stratus-cache/guardian-pipe-source-r4-20260927`. Независимый аудит подтвердил
   1234 новых cases и совместимость семи прежних PS5 readsets/self hashes:
   198 из R2 + 664 из R3 + 1234 из R4 = 2096, по 1048 на каждой оболочке.
   Это совмещённый source checkpoint отдельных окон, не единый свежий прогон и
   не C#/native/full-protocol acceptance. Старые failures сохранены как failures.
   Старые plan/manifest/decision не редактировать для повторного использования.
   Прежний 512-case manifest квалифицирует только
   прежние bytes, не изменённый GuardianLaunch и не полную цепочку.
   Сохранить original path/native identity и generic caller validation; offered
   process/thread/Job/root slots остаются до physical P death. Затем закрепить
   конкретные исходники и матрицу отказов; прежние source PASS и план реализации
   не являются приёмкой этих изменённых bytes. Отдельно остаются native-квалификация
   L/Guardian pipes, writer isolation, внешнее ограничение L lifetime и
   accepted-output sealing.
   Это незавершённая code-only задача, не повод расширять разрешения или критерии.
   Перед любым новым тестовым исполнением получить точный независимый test-safety
   freeze; до него разрешены только code-only правки и чтение исходников/доказательств.
   Caller-supplied receipt/Boolean/digest и source-only PASS не заменяют эти доказательства.
   Для следующей source-интеграции внешнего надзора выбран фактический механизм,
   а не ещё один не подключённый owner. Прежнее уточнение порядка сохранено в
   `.stratus-cache/terminal-next-integration-20260927/SUBSTRATE-DECISION.md`;
   выбранный профиль и независимое решение теперь записаны в PROFILE-DECISION.md
   и PROFILE-REVIEW.md того же каталога. Debug-only-L — выбранный non-Job механизм
   для дальнейшей закрытой реализации и квалификации termination при фактическом
   выходе debugger thread, но не доказанный hard physical-exit bound при S hang
   или stalled kernel I/O и не shared P/L writer isolation. Требуются точный
   fault model и реальный writer backend с непрерывной защитой от первой записи
   до sealing. Held deny-write open имеет полезные documented share semantics,
   включая конфликтующие writable mappings, но не доказывает прошлый интервал.
   Разрешено дальнейшее code-only проектирование без ослабления требований;
   provisioning/debugger/native/Windows действия остаются отдельно согласуемыми.
   Границы исходных требований отдельно прослежены в REQUIREMENT-TRACE.md и
   независимо проверены в REQUIREMENT-TRACE-REVIEW.md в том же каталоге следующей
   интеграции. Универсальный конечный physical-STOP при произвольном зависании
   kernel или одновременном отсутствии прогресса всех надзирателей не установлен
   исходными критериями. Это не исключение обычных отказов L/S и не разрешение
   принимать неизвестный исход: внешний надзор pre-P/post-W, original P/W physical
   terminal, непрерывная writer provenance и полная приёмка остаются обязательны.
   Ограниченная design decision теперь фиксирует actual S→L birth/entry,
   покрытый интервал, отдельно S exit/non-progress и kernel/VM ограничения,
   неизменяемое начало, внешний бюджет, writer backend и доверенные/исключённые
   principals. Production watcher и build W/S — разные роли; их требования и числа
   нельзя подменять друг другом. Это решение не даёт разрешения на создание
   служб/учётных записей, изменение ACL/token или native-запуск. Независимый review
   допускает следующую связанную code-only реализацию, но не принимает будущие
   исходники: отдельный interface/owner/Boolean без actual caller-chain не считать
   завершённой интеграцией.
   Конкретная условная осуществимость и actual callsite deltas сохранены в
   `.stratus-cache/terminal-next-integration-20260927/WRITER-BACKEND-FEASIBILITY.md`:
   отдельно контролируемый broker/build principal, исходная per-build logon
   identity и полная token/object/handle защита. Этот прежний feasibility-отчёт сам
   по себе не был выбором backend или разрешением его создавать. Выбор теперь
   конкретизирован в WRITER-PROFILE.md и LIFETIME-PROFILE.md и независимо проверен
   вместе с PROFILE-DECISION.md в PROFILE-REVIEW.md. Устранён найденный пробел:
   S удерживает original L PROCESS_INFORMATION.hProcess с PROCESS_DUP_HANDLE
   отдельно от observation/stop copy 0x101001, до закрытия transfer admission,
   известного settlement всех original workers и physical L closure. P/W права
   не расширяются; outer P birth Job в S не копируется, чтобы не менять last-close
   termination. Неизвестный outcome остаётся quarantine, не completed STOP.
   Новый внешний ceiling 120000 ms и фазовые caps только ограничивают admission:
   существующий P60 продолжает действовать и при post-W capture, без нового grace.
   Частичная связанная source-реализация теперь сохранена в пяти новых файлах
   SecuritySession/Supervisor/SupervisorWire/SupervisorControl/LauncherControl и
   правках TerminalCoordinator/Producer/GuardianLaunch/Bootstrap.ps1. Exact pins
   и независимые REVIEW-INTEGRATION.md / REVIEW-SUPERVISOR.md находятся в
   `.stratus-cache/terminal-session-source-20260927`. Original P/W source copies
   отделены от прежних cleanup-owned slots и удерживаются до physical L exit;
   actual capability ACK связан с original Resume. Это не приёмка полного patch.
   Отдельный post-W source-срез теперь реализует явно версионированный v2 wire,
   original normal-terminal origin и немедленную owner-bound публикацию до обоих
   launch-copy retirement и capture. S admission связан с min(original outer120,
   original P60, original terminal +30s), без reset при receipt/poll/CAPTURED.
   Исходные P/W physical identity/zero exits и exit FILETIMEs независимо читаются
   через удержанные S capabilities; поздняя публикация ACK/reader settlement не
   прячет уже полученный terminal cap и не превращается в success. Старый v1 не
   расширен молча: entry/records v2, loader требует S v2 / coordinator v3.
   Record cap 1024 и aggregate 3072 не увеличены, P6/W8 rights/lists сохранены.
   Exact pins и bounded независимые reviews сохранены в
   `.stratus-cache/terminal-postw-source-20260927`; REVIEW-ROOT-AND-COVERAGE.md
   содержит 16 prospective требований, не выполненные cases или test-safety freeze.
   Закрыт именно отсутствовавший source link, не native/full acceptance. Старые
   partial-S/L evidence и source PASS не квалифицируют эти новые bytes.
   Следующее code-only действие — настоящий protected namespace/held ancestry owner, canonical
   original observations и обе shared P/L writer bindings до соответствующих
   записей; held image/loader и dedicated original S entry/profile closure.
   Затем — фактический sealer, передача принятого результата с success-only
   освобождением и полное original control/native/causal retirement. Промежуточный
   capture/partial integration остаётся non-accepted, неизвестный STOP — quarantine.
   Новые исходники не компилировались и не исполнялись, тесты не запускались;
   прежние R2/R3/R4 и frozen native доказательства не квалифицируют новые bytes.
   Неустановленные account SIDs/credential custody/binary pins пока должны
   оставаться недоступными за independent deny-all selection; это не препятствие
   написанию закрытых исходников и не разрешение брать значения из metadata или
   текущего Administrator. Точные wire/event/worker/security/sealing обязанности
   следующего patch перечислены в PROFILE-REVIEW.md; новый source-review и отдельный
   test-safety freeze обязательны до исполнения. Прежние source/native окна не
   продлеваются. Не начинать новый общий поиск механизмов или повтор неизменённых
   suites вместо этой связанной реализации.
3. Для native/immutable-image/load-gate/production/PAPER действий получить необходимые
   отдельные окна и разрешения. Затем пройти оставшуюся цепочку R0–R9. Закрытый source
   checkpoint не является приёмкой полного протокола, кандидата или исходного инцидента.

- Ближайший самостоятельный этап — code-only интеграция и независимая проверка исходников
  полного parent/bootstrap-протокола на актуальном коде и подготовка матрицы отказов.
  Source-проверка не заменяет фактическую квалификацию этой матрицы в согласованной среде.
  Сохранять посторонние изменения, защитные entry blockers и действующие критерии приёмки.
  Suspend-count correction и направленные регрессии на текущем runtime завершены
  только как source-only этап (64/72/134/48 на PS5/PS7); native-протокол не принят.
  Ближайшая последовательность работ:
  1. Удержание входов и связь с compiler-bootstrap проверены только как source-этап
     (92/92 на PS5/PS7). Исходник передаёт реально захваченные bytes в строгий v2 validator,
     удерживает входы до physical compiler cleanup и перечитывает их для нового snapshot.
     Native-поведение не проверялось; не повторять этот неизменённый завершённый эксперимент.
  2. Реализовать полный disposable build producer/coordinator и отдельный watchdog, используя
     исходные удерживаемые process/Job handles. Watchdog должен подтвердить наблюдение
     producer до получения compiler Job; ACK Job/root предшествует compiler Resume.
     Job.active==0 не доказывает STOP, пока producer способен завершить новое создание.
     Аварийный путь должен закрывать такую возможность и подтверждать фактическую остановку.
     Фундамент startup-владения уже добавлен в source: новый P создаётся существующим
     CompilerCommand в собственном birth Job; только его приватный исходный handle может
     дать restricted stop-capability. Неизвестные duplicate/close outcomes удерживаются;
     cleanup предшествует освобождению input registration. Первый W launch/owner-watch
     и captured READY теперь реализованы в исходнике, но не запускались и не квалифицированы.
     W создаётся сразу running; suspended W мог бы остаться orphan при смерти L до Resume.
     Этот этап всегда non-success, private READY — только наблюдение, P Resume недоступен.
     Private transport и диагностический HELLO P/W теперь добавлены в source. Число handle
     из P не является handle в W: текущие stop-права 0x101001 не включают DUP_HANDLE.
     Эти исходники независимо проверены; наборы 190/128/50 и связанные 98 прошли обе
     оболочки без C#/native исполнения. Сохранены все прежние cases и два промежуточных
     отказа настройки input-теста; это не приёмка действующего канала или всего протокола.
     Следующий шаг — строгий independently approved контекст и явная retained-схема
     настоящей передачи compiler Job/root (отдельная capability либо original-handle broker).
     Будущий guarded P Resume допустим только после действующего owner-watch, проверенного
     контекста и безопасного private startup; сейчас он остаётся закрытым. Уже исполняющийся
     P завершает двусторонний private handshake до payload Open и compiler actions;
     этот handshake невозможен как условие первого Resume ещё suspended P.
     Затем P создаёт compiler Job; реальный W Job ACK предшествует compiler birth, а root/Job ACK
     предшествует compiler Resume. Не требовать созданный внутри P Job до первого P Resume.
     Первая ограниченная часть source-интеграции завершена: immutable shared startup
     origin до P native start и его передача W через новую строгую ABI v2; направленные
     source-наборы 140/100 и связанные 98 прошли обе оболочки, без C#/native исполнения.
     Не повторять этот неизменённый этап. Не называть этот срок build-wide:
     входы могли быть открыты раньше. Полное покрытие требует владения unopened payload
     inputs внутри P и проверенного контекста до их открытия. Перенос полного Open в L
     не решает задачу: W не имеет права остановить L. Предварительные P/W binary inputs
     должны быть отделены от payload и независимо квалифицированы. Зависание L до рождения W и задержки
     самого W не считать автоматически ограниченными одним таймером.
     Локальные pilot-часы W не заменяют исходный общий срок. Не заменять эту связь
     вызывающим Boolean, PID или файлом, объявленным подтверждением готовности.
     Success terminal должен завершать одноразовый P физически и проверять исходные
     P/W exits и полные EOF на внешнем launcher. Локальный compiler cleanup P не должен
     ждать W exit, если W ждёт P exit: такую взаимную блокировку не добавлять.
  3. Связать сборку с проверяемым контекстом окружения/сроков и изоляцией записи output.
     Случайный каталог, удержание ancestry или same-user ACL сами по себе не доказывают
     исключительного writer. Новые требования не добавлять как необязательные поля v2.
     Не выдавать самостоятельное одобрение producer за независимую квалификацию receipt.
     Общая metadata/preflight-проверка теперь выделена и вызывается самой фабрикой
     owner до CLR construction. Inventory выводится из raw документов внутри фабрики;
     caller SpecRecords и готовая preflight-проекция как вход больше не принимаются.
     Post-Open CaptureSnapshots → полный validator реальных bytes → AssertHeld сохранён.
     Проверки 148/79/114 прошли PS5/PS7; все прежние 100/65/98 cases сохранены.
     Проверенный этап не повторять без новой проверяемой причины; native не исполнялся.
     Это подготовительная граница, не доставка approved context внутрь standalone P.
     Bounded raw context delivery теперь связана с launch/entry исходниками через
     отдельные read-only section handles и строгие P v2/W v4 ABI. P/W независимо
     захватывают private bytes и сравнивают digest/length; HELLO/READY включают эти
     pins. W main не ждёт map/copy/hash worker и проверяет original clock/P identity
     до публикации. Это только source-интеграция byte correspondence; native не принят.
     Строгая versioned metadata schema и derivation теперь реализованы в shared compiled
     P/W исходнике и связаны с фактическими worker bytes; source-наборы 60/66/148/168
     прошли обе оболочки, независимый review выполнен. Это не исполнение C# parser
     и не независимо одобренный root или actual P-side coordinator с payload owner.
     Не повторять неизменённые проверки вместо следующей реальной интеграции.
     Нынешний HELLO/nonce, adjacent JSON или caller Expected не доказывают approval;
     v2 producer record не квалифицирует P/W/bootstrap binaries и environment/writer scope.
     Уточнённый ближайший порядок (2026-09-23): строгий versioned metadata envelope,
     разбор фактических private bytes и самостоятельный вывод inventory внутри compiled
     P/W worker; затем отдельная проверка независимого trust root. Старые v2 profile/plan
     сохраняют точные поля и канонические bytes, но не получают новых полномочий.
     Пока независимо установленного/квалифицированного root нет, selector обязан
     отклонять всё: semantic success не публикует admission/HELLO/READY и не разрешает
     Open/Resume/compiler. Не внедрять caller Boolean, Expected, environment override
     или придуманный digest вместо независимого источника. Не встраивать context hash
     в P/W так, чтобы manifest одновременно хешировал эти же артефакты (self-hash cycle).
     Parsing и root comparison остаются в удерживаемом background worker; W main
     продолжает original-P watch/STOP и не делает CopyBytes/parse/hash/Join.
     Реальный P partial coordinator теперь использует private derived inventory из
     исходного admitted capture и удерживает managed-only unopened owner до создания
     worker. Legacy owner reserved-path guard выровнен с parser, включая COM/LPT с
     superscript 1–3. Строгие P entry/control v3 и W launch/watch v5 добавляют отдельный
     ACK после наблюдения W точного P HELLO; отправка ответа P не считается его получением.
     READY не ждёт P HELLO: будущий первый Resume P должен предшествовать handshake.
     ACK остаётся observation, а не непрерывным надзором или Job/root/Open разрешением.
     Отдельный unconditional payload-Open blocker сохраняется даже при будущем root
     admission. Authored worker path проверяет actual retained snapshot bytes, их
     hashes/длины/пути и точные profile/plan/manifest bytes, затем AssertHeld; пока этот
     путь не исполнялся и не квалифицирован. Семь source/BCL-наборов 120/78/162/190/100/168/64
     прошли обе оболочки с первой попытки; независимый source-review завершён. Не повторять
     неизменённый эксперимент вместо следующей интеграции. Не называть её готовой сборкой.
     Реальные L-side P/W factory и pre-create boundaries теперь требуют один retained
     StartupPreloadOwner; произвольные public raw Prepare удалены. Descriptor выбирается
     без candidate/context/root аргументов; selector null и separate unconditional
     preload-Open blocker сохраняются. Два отдельных owner удерживаются до Open;
     actual preload snapshots проверяются до регистрации borrowers. Original P/W
     wrappers публикуются атомарно и доступны для явного cleanup даже после interruption;
     owner не запускает containment автоматически и не освобождает inputs до P/W disposal.
     Это declared-layout correspondence, не квалификация полного loader/search/config
     scope или ранее загруженного host/bootstrap; generic CompilerCommand не sandbox.
     Guarded first P continuation теперь связан в source: W no-argument coordinator
     удерживает W→P→command операции; private active phase/thread и exact original
     backlinks обязательны на свежих READY/capture/context/deadline boundaries.
     Generic Resume producer-mode отвергает. Shared native resume сохраняет counts
     1→2→1, повторяет original root/Job checks и не делает held-input IO после финальной
     W freshness. Отдельный unconditional first-continuation blocker остаётся закрыт;
     это не native admission или непрерывная атомарная гарантия жизни W. P HELLO/ACK
     по-прежнему следует после первого Resume, а compiler Job/root — более поздняя фаза.
     Дальше квалифицируемый точный P/W/bootstrap build/preload closure и независимый
     trust/environment/writer scope. CompilerCommand теперь разделён на удерживаемый
     PrepareBirthJob и StartSuspended без implicit fallback; реальные L-side P вызовы
     используют обе фазы с прежним сроком. Это outer Job самого P, а не Job compiler
     внутри P и не W ACK. Изменённый managed lifecycle имеет ABI v2; исходы создания,
     настройки и закрытия Job различаются, unknown не превращается в отсутствие.
     Независимый source-review завершён; source/BCL-наборы 120/190/100/84/80/62 прошли
     PS5/PS7. Отдельная первая неуспешная попытка нового tokenizer-теста сохранена;
     её test-only исправление независимо проверено до повторного запуска. Это не
     C# compilation/native acceptance и не открытие какого-либо защитного blocker.
     Интеграция после held-input verification теперь завершена и независимо
     проверена только на уровне source — retained compiler owner внутри исходного
     P invocation, без нового Stopwatch, с explicit parsed compiler role и отдельным
     independently bound environment; command публикуется до RegisterCommand,
     unknown registration не считается отсутствием и не разрешает release/native.
     Main не выполняет input IO, Join или cleanup worker; прежний original
     deadline и фактический reader-owned ACK остаются в одном объекте. Направленные
     регрессии обновлены без сокращения прежних case inventories, независимо
     проверены на безопасность и прошли 1020/1020 в обеих оболочках с первой попытки.
     Общий clockGate содержит original GetTickCount64, не payload IO, но source
     inspection не доказывает hard timing/lock-free прогресс. Selector и blocker
     остаются закрыты; native/compiler/W Job протокол не квалифицирован.
     Следующая описанная цепочка теперь связана и проверена только как source-этап:
     original Job → actual W Job ACK → compiler birth → actual root/Job ACK → Resume.
     Нативная квалификация и полный terminal по приоритетной очереди выше не завершены.
     PrepareOwned с новым local Stopwatch для этой цели непригоден. Для W-pull нужна
     отдельная capability оригинального P; нынешние stop rights 0x101001 не расширять.
     PROCESS_DUP_HANDLE даёт власть над таблицей handles, а не только одним Job: это
     отдельная квалифицируемая граница. Не передавать P DUP-capability самого W.
     Реальные P/W readers/writers должны поддерживать новые строгие one-use фазы;
     handle number/Boolean/echo не заменяет W-local native duplicate и actual ACK.
     Вместе с transfer изменить STOP: закрыть admission, доказать physical P death
     (рождения больше невозможны), затем terminate/recheck exact Job/root. Ранний
     empty Job и смерть P недостаточны, пока W удерживает Job duplicate. Unknown
     duplicate/close не разрешает retry или признание отсутствия; W main не ждёт worker.
     Compiler внутри P требует квалификации nested Jobs без breakaway fallback и
     прежнего предположения outer Job.active==1. Затем полный physical P/W exit/EOF
     terminal. Не открывать payload в L и не снимать
     payload/preload Open blockers в рамках одной установки trust descriptor.
     Новый 8-MiB carrier cap не заменяет component/envelope bounds. Source/DLL/reference
     payload не передавать внутри metadata carrier. P/W/bootstrap preload provenance,
     отсутствие иных writable views и изоляция L от посторонних inherit-all запусков
     подлежат отдельной квалификации; pre-W L stalls одним startup clock не ограничены.
  4. После code-only интеграции подготовить отдельно согласуемую квалификацию bootstrap,
     полного протокола, versioned R1 и exact-byte G/P/T import; затем чистую R0/R1 и R2–R9
     в предусмотренных окнах. Не обходить существующие WIP/pins/no-cache.
  Прежний набор 79/79 проверяет только source/pure контракт загрузчика, не исполняет
  C# bootstrap и не принимает весь протокол. Не заменять actual producer ещё одним
  неавторизующим JSON-контрактом. Не считать отдельный этап завершением полной цели.
- Уже принято: обе замороженные native-матрицы 56/56 на source commit
  `74e2fabdd63fe7f798703ee40e0ff364ee4724f6`; [checkpoint R2](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/windows-sandbox-native-check-20260914-r2/RESULTS.md).
  Не повторять закрытое окно для снятия прежнего блокера среды. Этот результат не заменяет
  проверку изменённого актуального кода или приёмку всего защитного протокола.
- После независимой приёмки полного протокола — подготовка и квалификация точной чистой
  ревизии и immutable R0/R1. Если необходимое действие выходит за имеющиеся разрешения,
  остановиться перед этим действием и запросить конкретный объём доступа/работ; публикация
  полного release-кандидата и запуск имеющихся Docker-сервисов не разрешены автоматически.
- Разовые согласования закрытых окон Sandbox, guest policy, reboot, закрытия приложений,
  остановки backup и пятифайловой публикации сохраняются в истории, но не являются бессрочным
  разрешением повторять эти действия. Новое изолированное окно согласовывается отдельно.
- Production и имеющийся test PostgreSQL остаются остановленными; не менять их состояние,
  режимы, образы, данные или защитные настройки. Будущие production R2–R6 и PAPER R7–R9
  требуют выполненных предпосылок и отдельных явных разрешений. LIVE и снятие kill switch
  Binance запрещены; прочие ограничения ниже полностью сохраняются.

Ближайший этап фиксировать отдельным проверяемым результатом: что принято на актуальной
ревизии, ссылки на доказательства и оставшиеся блокеры. Не выдавать его завершение за
завершение всей release-цели. Исторические поручения ниже не заменяют это актуальное задание.

Эта редакция фиксирует согласие пользователя на предложенные локальные работы и
изолированную проверку в GitHub Actions. Основание — сообщение в этой задаче:
«хорошо, тогда давай отредактируем описание и добавим там мои разрешения, там где они не обходимы».
Непосредственно перед согласием production-переход был явно оставлен отдельным
согласуемым шагом после технической приёмки. Нового разрешения на production нет.

## Описание цели

Довести STRATUS до принятого production-кандидата для Polymarket PAPER и закрыть
инцидент execution-evidence scheduler contention. Работать в пределах текущего P0,
сохраняя действующие критерии приёмки, защитные блокировки и исторические доказательства.
Это конечная release-цель, а не бессрочное наблюдение и не программа включения LIVE.

Выполнять работу по проверяемым этапам:

1. Завершить code-only исправления release-процедуры, включая код сетевого восстановления
   PostgreSQL, successor gate и parent-stage watchdog. Замороженный native prerequisite
   уже принят: обе оболочки 56/56, [checkpoint R2 от 2026-09-14](C:/Users/Administrator/Documents/weatherpoly/.stratus-cache/windows-sandbox-native-check-20260914-r2/RESULTS.md).
   Оставшаяся работа — завершить интеграцию всего защитного протокола на актуальном коде,
   полную матрицу отказов и независимую приёмку. Успех отдельных компонентов или
   инструментированного тестового стенда не считать приёмкой всего протокола.
2. Подготовить и квалифицировать точную чистую ревизию и неизменяемый образ по R0/R1:
   полная CI-проверка этой ревизии, PostgreSQL integration, Compose validation, Docker build,
   проверяемые исходные манифесты, рассмотренная оператором CI-запись и свежая R1-печать.
   Проверить сохранённые доказательства backup/restore и принятой цепочки `0046 -> 0047`
   без повторной миграции или восстановления production. Публикация полного release-кандидата
   не входит в узкое разрешение на тестовую ветку ниже; если она нужна, согласовать её отдельно.
3. После выполнения технических предпосылок и отдельного явного согласования production-окна
   пройти R2–R6 по действующему runbook: допуск сохранённого PostgreSQL `0047`, полную
   нагрузочную проверку, сверку данных и доказательств, согласованный successor epoch,
   API-only rollback isolation и проверку worker под включёнными блокировками.
4. Только после приёмки предыдущих этапов и согласования соответствующего PAPER-окна
   пройти R7–R9: защитная lease, 60-минутный Polymarket PAPER-canary с неизменяемыми
   контрольными точками T+0/5/15/30/45/60, предусмотренный terminal STOP, независимая
   проверка результатов, добавляемая без перезаписи запись приёмки и обновление roadmap.

Цель завершена только после всей этой цепочки: своевременная обработка execution evidence
подтверждена, нет дубликатов, нарушений целостности и критических ошибок, аварийная остановка
проверена, canary завершён предусмотренной остановкой, доказательства сохранены и инцидент
закрыт по результатам приёмки. Отсутствие естественных сделок учитывать отдельно: оно не
доказывает качество исполнения и не разрешает создавать синтетические production fills.

## Уже предоставленные разрешения

- Самостоятельно читать проект и диагностические данные, исправлять относящийся к P0 код,
  тесты и документацию, выполнять изолированные локальные проверки и готовить артефакты
  без изменения production. Сохранять посторонние изменения рабочей папки. Эти разрешения
  не включают запуск имеющихся Docker-сервисов или тестового PostgreSQL.
- Создать и опубликовать новую изолированную ветку `codex/parent-stage-native-check-*`
  в `GrggrT/stratus` исключительно из пяти файлов подготовленного payload:
  `scripts/Stratus.ParentStage.Runtime.cs`,
  `scripts/Invoke-StratusParentStageWatchdog.ps1`,
  `scripts/tests/Test-StratusParentStageWatchdog.ps1`, `.gitattributes` и
  `.github/workflows/parent-stage-native-check.yml`.
  Исходный пакет находится в `.stratus-cache/native-host-check-20260914/payload`;
  его замороженные байты и хеши проверяются перед публикацией.
- Запустить относящуюся к этой ветке подготовленную Windows-матрицу GitHub Actions
  (PowerShell 5.1 и PowerShell 7, все 56 случаев, лимит 10 минут на job), прочитать и
  сохранить результаты, журналы и артефакты. Использовать только штатный доступ и существующие
  лимиты; не покупать мощности, не менять тарифы, repository settings или secrets.
  Не переносить в эту ветку остальную рабочую папку, общий CI или production-данные.
- Повторно не запрашивать эти же разрешения при неизменном объёме действий. Разрешение
  пользователя не доказывает наличие прав GitHub/Actions и не гарантирует пригодность runner.
  Если нужного штатного доступа нет, сообщить точный недостающий доступ; не извлекать
  сохранённые учётные данные для обходного API-доступа.

## Не разрешено этой редакцией

- Запускать, останавливать, пересоздавать или удалять production-контейнеры, менять их
  образы, перезапускать Docker; это относится также к backup, `safety-control`, Binance
  stream, Telegram и имеющемуся тестовому PostgreSQL. Обнаруженное отклонение не исправлять
  автоматически; healthy-статус backup не доказывает свежий backup без БД. Прежняя остановка
  backup была отдельно согласованным закрытым окном, а не постоянным разрешением.
- Менять production-режимы, `.env`, роли, ACL/HBA, применять миграции/restore, активировать
  successor epoch, снимать global/Polymarket kill или возобновлять PAPER вне отдельно
  согласованного окна. При остановленном production PostgreSQL не читать `runtime_control`.
- Включать LIVE, снимать kill switch Binance, расширять работу на Binance или новые модели.
- Сливать или публиковать изменения в main/release-ветки, создавать release-теги, выполнять
  deploy или использовать разрешение на пятифайловый тест как разрешение на такие действия.
- Обходить защитные ограничения платформы или Windows Job, ослаблять ANY-Job проверку,
  удалять защитные entry blockers без приёмки протокола, пропускать обязательные тесты,
  увеличивать допуски ради PASS или переписывать исторические результаты.

Production-окно запрашивать с конкретными контейнерами, точной ревизией/образом, действиями,
сроком и процедурой STOP/rollback. Оно начинается только после явного согласия и выполненных
предпосылок; разрешение не заменяет доказательства. R1 остаётся предпосылкой с
`section3_authorized=false`, а не разрешением запустить PostgreSQL.

## Правила продолжения и учёта результата

Порядок и критерии приёмки определяют `docs/PRODUCTION_READINESS_CHECKLIST.md`,
`docs/RELEASE_CUTOVER_RUNBOOK.md` и R0–R9 в `docs/QUALITY_ROADMAP.md`. Эта редакция уточняет
полномочия, но не ослабляет эти документы и не расширяет цель до общего product backlog.

После каждого этапа фиксировать фактический результат, проверенные артефакты и следующий
шаг. Новые требования добавлять только из-за конкретного дефекта, мешающего приёмке.
Не считать мониторинг продвижением release-работы; не повторять одинаковый неудачный
эксперимент без изменения входных условий или новой проверяемой гипотезы. При исчерпании
разрешённых путей сообщить конкретный блокер и необходимое действие пользователя.

## Исторический журнал — не текущее задание

Все следующие хронологические разделы сохраняют прежние состояния, поручения и границы
разрешений как историю. Формулировки «45/56», «ещё не выполнены» и «ещё не полученное
разрешение» относятся к описываемому тогда моменту, а не к текущему checkpoint. Они не
требуют повторять завершённые этапы и не продлевают разовые разрешения. Для продолжения
использовать актуальное задание в начале документа и последние принятые результаты.

На момент этой редакции локальная native-матрица остаётся 45/56 в каждой оболочке;
11 отказов независимого запуска не закрыты. Инструментированные gate-наборы 133/133
не отменяют прежние необъяснённые сбои и не являются полной native/bootstrap-приёмкой.
Публикация пятифайловой ветки и внешний запуск ещё не выполнены. R0–R9 не объявлены принятыми.

При сохранении этой редакции текст карточки цели в Codex автоматически не заменялся:
доступные инструменты позволяют читать цель и менять только её статус, но не редактировать
objective. Тогда карточка оставалась `blocked`; цель не завершалась и не пересоздавалась
для изменения текста. Указанные выше разрешения действуют в этой задаче независимо от
отображения формулировки карточки.

## Продолжение 2026-09-14 после возобновления

После предложенной ассистентом команды `/goal resume` чтение состояния показало
`status=active`, но `objective=resume`: в этой версии приложения слово стало текстом цели.
Подсказка ассистента не дала ожидаемого результата. Сокращённая запись не меняет намерение
пользователя; работа продолжается по полной цели и границам этого документа. Никакая
часть настоящей release-цели не объявлена завершённой. Автоматически исправить objective
доступным инструментом нельзя; внутреннее хранилище приложения не изменялось.

Разрешённая пятифайловая ветка `codex/parent-stage-native-check-20260914T163030Z`
опубликована, и удалённая ссылка подтверждает commit
`93f78f6164f1726d926f991025c83fc637c678f4`. Release-ветка не менялась. Статус Windows jobs
пока не подтверждён: connector не имеет доступа, а браузер требует входа в GitHub.
Проверяемая запись: `.stratus-cache/native-host-check-20260914/PUBLICATION.md`.
Следующий шаг — получить штатный доступ к результатам, проверить запуск именно этой
ревизии и сохранить доказательства; повторная публикация без наблюдения не требуется.

Позднее доступ через браузер появился. Run `34869135356` оказался отклонён до тестов
из-за выражения `matrix.shell` на уровне step. Исправлена только настройка workflow,
commit `38700391b2235d2340a696777c4e37f6b17c9e41`; три замороженных source/test файла
не менялись. Run `34870507190` выполнил оба полных набора: по 45 PASS / 11 отказов,
exit 1, очистка подтверждена. Артефакты скачаны и сверены с SHA-256 в GitHub.
Все отказы относятся к созданию независимого watchdog; точный код Windows-ошибки
не сохранился в исходном агрегаторе. Для его фиксации готовится изменение только
диагностики workflow, без изменения тестов, launcher, Job policy или критериев.
Доказательства и границы вывода: `.stratus-cache/native-host-check-20260914/RUNS.md`.

Диагностика опубликована только в том же workflow, commit
`1f923934dde7c2673af4281cf74144a9aee249fb`. Run `34871653269` снова завершился
45 PASS / 11 отказов в обеих оболочках с подтверждённой очисткой. В каждом отчёте
сохранены 11 ошибок CreateProcess с Windows code 5. Это подтверждает отказ доступа,
но само по себе не доказывает конкретную Job policy или иную причину. Артефакты и
SHA-256 проверены; тесты и native-код не менялись. Следующая диагностика ограничена
чтением сведений о существующих ограничениях runner; обход или изменение этих
ограничений не разрешены. Полная цель и production-переход остаются непринятыми.

Run `34872372384`, commit `74e2fabdd63fe7f798703ee40e0ff364ee4724f6`, завершил
разрешённую диагностику существующего Job: в PS5/PS7 до и после полного набора
подтверждены `in_any_job=true`, успешный ответ размером 144 байта, `LimitFlags=0`,
оба разрешающих breakaway-флага false. Результат остаётся 45/56, 11 code-5 отказов,
очистка подтверждена. Эти runners не допускают необходимый независимый запуск.
Повторять неизменённый эксперимент или обходить/менять Job policy не следует.

Для следующего принятого этапа необходимо отдельно согласовать конкретную изолированную
Windows-среду, где независимый watchdog разрешён штатно, и доступ к ней. Это не разрешение
на покупку ресурсов, изменение repository/security settings или production. На такой
среде нужны неизменённые 56/56, exit 0 в обеих оболочках, все 11 lifetime-случаев,
`any-Job=false` и очистка; затем всё ещё остаётся интеграция полного защитного протокола
и R0–R9. Текущая диагностика не объявляет полную цель завершённой.

## Подготовка Windows Sandbox 2026-09-14

После отсутствия у пользователя отдельной Windows-машины/VM согласована только подготовка
изолированного пакета для Windows Sandbox. Это не разрешение включить компонент Windows,
открыть Sandbox, выполнить гостевые тесты, перезагрузить компьютер или менять Docker.
Существующие ограничения production, LIVE и kill switches сохраняются полностью.

Подготовлен `.stratus-cache/windows-sandbox-native-check-20260914`: frozen3, официальный
переносимый PowerShell 7.6.5, гостевые сценарии с явными guard/opt-in, неактивный
`Sandbox.wsb.template` без автозапуска, отдельные входной и выходной каталоги и инструкция.
Все 667 входных файлов сверены; host-only проверка пакета и по 51 чистой проверке
XML/ZIP-name/AST прошли в PS5/PS7. Гостевые сценарии, загруженная PS7 и native-тесты не
исполнялись. Зафиксировано `PREPARED_ONLY`, а не 56/56 или приёмка протокола.

Итог и хеши: `.stratus-cache/windows-sandbox-native-check-20260914/PREPARATION.md`;
фактические результаты подготовки: `preparation-checks.json` в том же каталоге.
Sandbox остаётся выключенным; последнее измерение показало 3234 MiB доступной RAM и
88% commit. До запуска нужен согласованный ресурсный план и отдельное окно включения
компонента/возможного reboot с сохранением stopped production и проверкой после загрузки.
Sandbox пока лишь кандидат совместимой среды; обход Job policy или ослабление тестов
не допускаются. Общая цель не завершена, её статус и objective этой подготовкой не менялись.

### Отдельно разрешённое включение компонента Sandbox

2026-09-14 пользователь явно подтвердил штатное закрытие Chrome и включение Windows
Sandbox с обязательными компонентами **без автоматической перезагрузки**. Это разрешение
не охватывает открытие Sandbox, гостевые тесты, остановку Codex/Docker/WSL или контейнеров,
изменение restart policies и production.

Chrome закрыт штатно, без force. Единственная операция включения
`Containers-DisposableClientVM -All -NoRestart` успешно завершилась в 17:58:54 UTC,
`RestartNeeded=true`. В 18:00 UTC компонент Enabled, перезагрузки не было. Production
API/PostgreSQL/worker и все 14 контейнеров не изменились по ID/image/state/restart/OOM/
restart policy; работает только прежний backup. Ничего из гостевого пакета не запускалось.

Checkpoint: `.stratus-cache/windows-sandbox-native-check-20260914/HOST_ENABLE.md` и
`host-enable-20260914T175758Z/{before,after}.json`. Следующая перезагрузка требует отдельного
согласования с учётом прерывания этой сессии, Docker/WSL и backup; затем нужна read-only
проверка до открытия Sandbox. Включение компонента не доказывает native 56/56,
не меняет блокировки протокола и не завершает release-цель.

Затем пользователь отдельно подтвердил перезагрузку Windows сейчас, включая ранее
объяснённое прерывание этой сессии, Docker/WSL и работающего backup. В 18:04 UTC сохранён
предоперационный снимок: drift отсутствует, production остановлен, backup содержит только
`sh`/`sleep`. Подготовлен один штатный запрос reboot без force; сам checkpoint не доказывает
завершение перезагрузки. Порядок возвращения: `.stratus-cache/windows-sandbox-native-check-20260914/REBOOT_CHECKPOINT.md`.
После входа сначала read-only проверка, без самостоятельного запуска Docker/контейнеров.
Открытие Sandbox, гостевые тесты и production-переход этим разрешением не охвачены.

После сообщения пользователя о завершённой перезагрузке read-only проверка 18:38–18:45 UTC
подтвердила новую загрузку Windows 2026-09-14T18:06:55.5000000Z и Sandbox Enabled. Пакет
повторно сверён PS5/PS7, гостевые тесты не запускались. Production API/PostgreSQL/worker,
их exact IDs/images/digests/restart/OOM не изменились и остаются stopped. Backup снова
запущен в 18:38:06Z; источник запуска не установлен, ассистент не выполнял lifecycle команд.
RAM доступно около 2.9 GiB, ниже выделяемых Sandbox 4096 MiB. Переход приостановлен до
согласования остановки только backup и освобождения памяти выбранными приложениями.
Подробности: `.stratus-cache/windows-sandbox-native-check-20260914/POSTBOOT.md` и
`reboot-20260914T180411Z/after.json`. Разрешения на Sandbox/tests/production всё ещё нет.

### Отдельное разрешение на освобождение ресурсов после reboot

Затем пользователь ответил «разрешаю» на остановку **только** backup-контейнера и
штатное закрытие Discord/Steam. Разрешение не включает force приложений, остановку или
перезапуск Docker/WSL/Codex, другие контейнеры, изменение restart policies, Sandbox/tests
или production. Выполнена одна команда stop по exact ID backup: после 30 секунд Docker
сам применил SIGKILL, exit 137, OOM=false; graceful shutdown не состоялся. Steam завершён
обычным выходом из меню. Окно Discord закрыто штатно, но 6 фоновых процессов остались;
принудительное завершение, logout и изменения настроек не выполнялись.

На 18:55:17 UTC все 14 контейнеров stopped. Независимая сверка не выявила дрейфа остальных
13, production images/digests, restart/OOM/policies и engine. Доступны 5611 MiB RAM,
commit 52%; это не гарантия ресурса на будущее. Sandbox не открыт, активного .wsb нет,
evidence пуста, guest/native тесты и загруженная PS7 не исполнялись. Перед следующим
запуском требуется явное согласование изолированного окна и повторная проверка ресурсов.
Результаты: `.stratus-cache/windows-sandbox-native-check-20260914/RESOURCE_PREP.md` и
`resource-prep-20260914/{backup-stop,after}.json`. Полная цель не завершена; её статус и
objective этой операцией не изменялись.

### Разрешённое окно Sandbox: preflight отказ 2026-09-14

После нового явного «да» разрешено открыть подготовленную изолированную Sandbox и выполнить
её тесты без production. Прежний запрет менять/обходить защиты при отказе среды сохранён.
Пакет повторно сверён, все 14 контейнеров stopped без дрейфа, RAM >5 GiB. В 19:01:33 UTC
выполнен один запуск exact-template .wsb без автозапуска. Первый запуск Windows автоматически
установил Store-пакет MicrosoftWindows.WindowsSandbox 0.8.107.0 и штатный wsb CLI; отдельная
команда установки/обновления не выполнялась. Это изменение Windows-компонента отражено в аудите.

Гость открылся. Только read-only probe через официальный CLI ExistingLogin подтвердил
WDAG/x64/PS5.1/input667/manifest/csc/отсутствие включённых сетевых адаптеров, но effective
ExecutionPolicy=Restricted, ready=false, guest exit1. В соответствии с границами выполнение
остановлено: PrepareTools, загруженная PS7, wrappers и native-набор не запускались, политика
не менялась. Inline-команды применялись только для диагностического чтения и одного нового
JSON, не для исполнения заблокированных тестовых файлов. Независимый host-only аудит
подтвердил целостность пакета и единственный невыполненный predicate probe — Restricted.

Evidence сохранено на хосте и сверено до закрытия. В 19:05:29 UTC exact guest остановлен
одной штатной командой wsb stop; sessions пуст, vmmem гостя отсутствует. Все 14 контейнеров,
production images/digests, restart/OOM/policies и engine неизменны. Остался штатный
WindowsSandboxServer broker, он принудительно не завершался. Полная цель остаётся невыполненной;
карточка blocked/objective=resume автоматически не изменялась.

Результат: `.stratus-cache/windows-sandbox-native-check-20260914/SANDBOX_WINDOW.md`,
`launch-20260914T1901Z/{before,result}.json` и `evidence/guest-preflight-20260914T1901Z.json`.
Предложение гостевого CurrentUser RemoteSigned в новом одноразовом окне — отдельное
ещё не полученное разрешение, а не автоматическое изменение существующих границ.

### Гостевой CurrentUser RemoteSigned разрешён; обнаружен дефект подготовки

Пользователь отдельно подтвердил guest-only CurrentUser RemoteSigned в новой одноразовой
Sandbox и последующий запуск неизменённых тестов. Host/LocalMachine/Group Policy/Process
policy, Job, ProtectedClient, сеть, mappings, frozen tests и production этим не разрешены
к изменению. После нового baseline и независимой проверки пакета в 19:13:03 UTC открыт
новый guest ID `49ba1367-e8fd-4234-8445-56a801ea13eb` с exact-template .wsb без autorun.

Гостевая PS5 сменила только CurrentUser Undefined→RemoteSigned, эффективная политика
подтверждена; остальные scopes Undefined. Затем единственный PrepareTools вызов отказал
до создания гостевой копии PS7: Unable to find type [IO.Compression.ZipArchive]. Причина
в подготовительном verifier: загружается только System.IO.Compression.FileSystem, затем
прямо используется тип из System.IO.Compression. Независимый source review и чистый host
PS5 type-resolution probe подтвердили ошибку; native/guest helpers в host probe не исполнялись.
Предыдущий host validator проверял другой путь ZipFile.OpenRead и синтаксис helpers.

Native-тесты не стартовали, PS7 не подготовлена/не исполнена, её guest policy не менялась.
Повтора/исправления пакета в этом окне не было. Сохранён guest cleanup snapshot: никаких
test helpers/fixtures/staging и C:\StratusTools. В 19:18:04 UTC гость закрыт после сохранения
evidence. Итоговая сверка всех 14 контейнеров, production digests, engine и всех host PS5/PS7
policies без дрейфа. Production/LIVE/migrations/runtime_control/kill switches не затронуты.

Минимальное следующее code-only исправление относится к helper загрузки сборок и новым
проверкам чистой PS5; frozen3 и критерии не ослаблять. Результаты текущего остановленного
окна: `.stratus-cache/windows-sandbox-native-check-20260914/REMOTESIGNED_WINDOW.md`,
`run-20260914T1910Z/{before,result}.json` и новые files в evidence. Полная цель не завершена;
карточка blocked/objective=resume не менялась. Новый запуск исправленной ревизии ещё не выполнен.

### Исправленная R2: обе native-матрицы 56/56, гость закрыт

Пользователь явно разрешил исправить подготовительный helper, перепроверить пакет и повторить
окно с неизменёнными тестами и защитами. Отдельная R2 не изменяет R1 и её evidence. Ровно два
из 667 input-файлов отличаются: явная загрузка System.IO.Compression в verifier и только её
SHA в manifest. Frozen3, остальные helpers и tools прежние; прямой ZipArchive regression
в чистых host PS5/PS7, package validator и по 51 pure guard прошли независимо.

В 19:27:47 UTC создан новый guest `a611ebcc-6760-43fb-9905-37ef468b0c96`: 4096MB,
ProtectedClient Enable, network/redirect channels disabled, input RO/evidence RW, no autorun.
PS5 сменила только разрешённый guest CurrentUser Undefined→RemoteSigned. Настройщик PS7
отказал до записи, потому что официальный portable-пакет уже содержит LocalMachine
RemoteSigned. Read-only диагностика и сверка config с закреплённым ZIP подтвердили это;
настройщик не повторяли, policy не меняли, для тестов использована прежняя RemoteSigned.
Отказ setter записан отдельно и не скрывается успешным результатом native-набора.

Оба полных неизменённых native-набора выполнены по одному разу: PS5.1 — 56/56 за 49,573 с,
PS7.6.5 — 56/56 за 37,903 с; suite/wrapper/guest/CLI exit0. Независимый read-only аудит
подтвердил 56 уникальных AST-имён = JSON = transcript, initial/staged/final source hashes,
portable658 до/после, budget, cleanup/staging, отсутствие native-create ошибок и все прежние
11 не прошедших GitHub lifetime/readiness/owner-death случаев. Shell Job snapshots false
не выдаются за доказательство отсутствия Job у любого процесса: независимость watchdog
проверяется неизменённым native AssertNoJob и соответствующими lifetime-тестами.

Все 15 evidence-файлов сверены SHA до/после штатного закрытия гостя в 19:41:12 UTC.
CLI environments пуст. Оставшиеся WindowsSandboxServer/vmmemCmZygote/vmmemWSL не завершались
force. Host postflight 19:41:25 UTC: все 14 контейнеров stopped, полный прежний baseline
по IDs/images/digests/restart/OOM/policies/engine и host PS5/PS7 scopes без дрейфа;
доступно 6235 MiB RAM, commit 54%. Docker lifecycle, production, LIVE, migrations,
runtime_control и kill switches не затронуты, тесты/thresholds/Job/network не ослаблены.

Результат: `.stratus-cache/windows-sandbox-native-check-20260914-r2/RESULTS.md`,
`run/{result,independent-audit,after,close}.json` и `evidence/`. Проверка относится к frozen
source commit `74e2fabdd63fe7f798703ee40e0ff364ee4724f6`, а не к свежей release-ревизии.
Native prerequisite принят; `full_parent_protocol_accepted=false`,
`production_authorized=false`, R0–R9 и полная цель не объявляются завершёнными.
Следующий этап — полная интеграция/приёмка parent/bootstrap-протокола на актуальном коде,
затем чистые immutable R0/R1; production и PAPER-окна по-прежнему согласуются отдельно.
Карточка blocked/objective=resume автоматически не редактировалась и не завершалась.
