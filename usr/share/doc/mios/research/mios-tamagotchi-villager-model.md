<!-- AI-hint: Evidence-backed design notes for original personality-driven MiOS-Mons; distinguish Nintendo statements, community reverse engineering and our implementation decisions. -->
<!-- AI-related: tools/native/mios-tamagotchi, specs/tamagotchi-tui, task_3716, task_3717 -->
# Villager research and the MiOS-Mon model

Research checked 2026-11-04. This research informs original MiOS characters,
not a port of Nintendo character assets or dialogue.

## What the sources establish

Nintendo's New Leaf developer interview describes selecting animal traits to
fit a character's role and setting. Familiar animal forms and culturally
recognizable associations help communicate a role before dialogue begins.
That supports readable silhouettes, expressions and activity cues in a small
voxel character. [Nintendo, Iwata Asks: Battling With Volume](https://iwataasks.nintendo.com/interviews/3ds/animalcrossing-newleaf/0/2/)

Nookipedia documents six early personality pools and the addition of smug and
big-sister personalities in New Leaf. It describes dialogue as personality
pools with individual and hobby variation, and two subtypes per personality in
New Horizons. This is community documentation, not a Nintendo implementation
specification. [Nookipedia: Villager](https://nookipedia.com/wiki/Villager)

The community account of New Horizons mystery tours says selection chooses a
species and then an individual within that species. Species and individual
selection therefore have distinct distributions. Starting-resident and
campsite constraints are separate systems; they should not be generalized to
every villager-generation path. [Nookipedia: Mystery Tour](https://nookipedia.com/wiki/Mystery_Island),
[Nookipedia: Moving](https://nookipedia.com/wiki/Moving)

## Implementation decisions

These are our design choices inferred from the research:

- Ship at least sixteen original MiOS-Mons, with eight clearly different animal
  body families and two individual variants each. Names, palettes, hobbies,
  dialogue and thoughts are original.
- Separate stable identity, animal appearance, personality, hobby and temporary
  state. The same personality can occur in different bodies and life stages.
- Use eight gender-neutral voice archetypes: cozy, athletic, grumbly, showy,
  neighborly, sunny, refined and protective. They are inspiration categories,
  not a claim to reproduce another game's dialogue algorithm.
- Select a new identity with a seeded species/individual decision and retain it
  across saves, reloads and growth. Existing schema-1 pets retain their state
  during a versioned migration and receive the original default identity.
- Dialogue prioritizes actual state: egg, death, illness, hunger, sleep/low
  energy and growth. Healthy conversation and thoughts then vary by the
  individual's personality and hobby. Presentation never changes memory
  answers, age, needs or coins.
- Autonomous movement and turning follow life stage, needs and a separate
  visual RNG. Controllers and keyboards operate menus and care HUDs only.
- Render voxel volume, facial relief, ears, tails, shells, beaks and props on
  the CPU. The same indexed image can be rendered in Unicode, ASCII, console,
  remote-terminal or embedded web-terminal views without a GPU.

## Dialogue transport

The default readable fallback is an inline thought/dialogue area below the pet
view. Native tmux popup/menu interaction is available only after verifying the
attached client, focused terminal and owning pane. Right-click uses the game's
own terminal mouse reporting and native tmux menu vocabulary; it does not
replace another application's global bindings.

Restricted clients, tiny terminals, missing popup capability, lost focus or
unattached sessions keep inline output. Redirected snapshots include plain
ASCII dialogue/thought text, remain read-only, and do not spawn interactive
windows or controller probes. Mobile and smartwatch terminal webviews use the
same compact layout. Actual device/browser acceptance is recorded separately
from transport compatibility.


## Follow-up: bodies, faces and room identity

Rechecked2026-11-04. Community Villager and Mystery Tour references continue
to distinguish personality dialogue pools, individual/hobby variation and
species-first selection for that particular recruitment path. The Nintendo
New Leaf interview endpoint returned502 during this recheck; retain its prior
citation as historical evidence, rather than treating this retry as success.

Nintendo's current neighbor guide supports conversation and friendly
interaction as the relationship loop; it does not specify internal generation
algorithms. [Nintendo: Good neighbors](https://play.nintendo.com/news-tips/tips-tricks/animal-crossing-new-horizons-neighbors-tips/)

Double-height mature bodies, ASCII kaomoji, eight original themed rooms and
sixteen earnable/purchasable decorations are our design decisions. They are not
claims to reproduce Nintendo's rendering or economy. Each pet's stable room
seed is independent of gameplay RNG, while actual wins/age unlock rewards.
Explicit coin purchases and placement use the same HUD/native-menu controls;
they do not provide direct locomotion. Faces remain terminal-safe ASCII and
dialogue retains inline paging on constrained clients.


## Care-funded skill growth and chibi rendering

The SEGA-authored Sonic Adventure 2 instruction manual describes egg-to-maturity
growth, care affecting a creature's behavior, items used in development,
kindergarten activities and time passing while the garden is visited. This is
primary manual content available through a third-party reprint, checked
2026-11-04. [SEGA, Sonic Adventure 2 manual, pages 26..29](https://manualzz.com/doc/1393870/sega-sonic-adventure-2-video-game-instruction-manual)

MiOS-Mon's generated syllable names, independent hatch RNG, five innate stat
rolls, E..A aptitudes, XP/level formulas, care costs, bounded rewards and species/
personality palettes are original design decisions. The manual supports the
care/development inspiration; it does not establish these formulas or a claim
to reproduce another game's internal genetics.

The operator rejected the previous tall body and ambiguous face in a real
live screenshot. The replacement uses a broad head, a short common torso,
stub limbs and species-specific ears, nose/muzzle and relevant tail. The face
is a code-native ASCII stroke texture sampled from the head's visible
depth-tested surface; it is not a screen-aligned label. This preserves actual
rotation, foreshortening, sleep roll and occlusion. Portrait/landscape layouts
use native workspace golden proportions with minimum control budgets.

## Reference review for the doll redesign

Nintendo's official Rover figure provides a primary visual reference for the
cat's broad head, pointed ears, compact torso and short limbs. These are our
visual observations and design inspiration, not Nintendo's stated modeling
rules. The original MiOS-Mon has its own geometry, palette and expression atlas.
[Nintendo: Rover](https://www.nintendo.com/us/amiibo/detail/rover-amiibo-animal-crossing-series/)

Nintendo-authored CEDEC 2020 development slides, reproduced by an independent
publication, also show cat silhouette and face variations. They were inspected
as visual references; none of their assets are included in the executable.
[Cat design slide](https://images.p-nintendo.com/o/2020/09/5f54a8559d3cc2.jpg),
[Variant design slide](https://images.p-nintendo.com/o/2020/09/5f54a8552fb1c2.jpg)

The Met describes the broad face, triangular nose and simple openings of a
sixth-century haniwa warrior head. This supports an independent Japanese clay
doll design reference. It does not establish that every Animal Crossing
villager descends from haniwa or that all villager proportions follow one
historical doll type. Folklore motifs and readable animal anatomy are separate
influences; the prototype's quantitative head/body proportions are our design
calibration.
[The Met: Head of a Warrior Clay Tomb Sculpture](https://www.metmuseum.org/art/collection/search/44833)

Rechecked 2026-11-04: Nintendo's Rover and The Met pages were available; the
New Leaf developer interview again returned 502. Prior citations remain
historical evidence. Actual live cat front/oblique/side/rear appearance is a
separate acceptance gate; unit coverage or roster screenshots alone do not
override the operator's rejected graphics.
