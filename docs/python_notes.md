# Python / code-structure notes (AS's personal reference)

Explanations of Python and SQLAlchemy constructs encountered while
reading the CalibrationNet code, written down so they don't have to be
re-derived. Personal notes, not collaboration documentation — decide
before public release whether this file ships or stays local.
Add entries as new constructs come up during cleanup.

---

## Dunder names (`__something__`)

Names with double underscores on both sides (pronounced "dunder", for
*double underscore*) are "magic names some machinery looks up" rather
than functions you call directly. Two flavors appear in this codebase:

- **Python's own hooks** — the language itself calls them at defined
  moments (`__repr__`, `__init__`, `__eq__`, ...).
- **Library conventions borrowing the style** — e.g. SQLAlchemy's
  `__tablename__`. Not special to Python; special to SQLAlchemy, which
  looks the name up on each model class. The dunder style keeps these
  configuration names visually distinct from (and unable to collide
  with) real data attributes like column names.

## `__repr__` (every file in calibrationnet/models/)

A method Python calls whenever it needs a developer-facing text version
of an object: echoing it in an interactive session or notebook,
printing a list containing it, showing it in a debugger or log.
Without it, model objects print as
`<calibrationnet.models.run.Run object at 0x104f3b9d0>` (a memory
address); with it, they print `Run(run_number=9469)`.

Conventions it follows:
- the return string mimics a constructor call (the recommended style
  from the Python docs — it reads as "the code that would recreate
  this object");
- include only the *identifying* fields (the natural key), not all
  columns — reprs appear in bulk when inspecting query results, so
  they must stay short.

## `__tablename__` (every file in calibrationnet/models/)

A plain string attribute (NOT a method — nothing is called, nothing
"returns"; compare `__repr__`, which is a method Python invokes):

    class Run(Base):
        __tablename__ = "runs"

SQLAlchemy's declarative system reads it off each model class to learn
which database table the class maps to. Every piece of SQL the ORM
generates for the class — SELECT/INSERT/UPDATE, joins, and the Alembic
migration autogeneration — writes this string as the table name.

What it avoids:
- **Guessing.** SQLAlchemy deliberately does not derive a table name
  from the class name (some frameworks pluralize automatically:
  Run -> "runs"). Making it explicit means renaming a Python class can
  never silently rename — or disconnect from — a database table.
- **Name-style mismatch.** It decouples Python conventions (singular
  CapWords class: `Run`, `RunSegment`) from SQL conventions (plural
  snake_case table: `runs`, `run_segments`).
- **Collisions.** The dunder style keeps SQLAlchemy's configuration
  attributes in a separate namespace from column attributes, which are
  plain names on the same class.

Omitting it on a model class is an immediate error at import time
("could not assemble any primary key columns" comes later; the missing
tablename error comes first) — it is required, not optional.

## `__init__.py` (one per package directory)

The file that turns a directory into an importable *package*: Python
will only treat `calibrationnet/models/` as something you can import
from because `models/__init__.py` exists. Its body runs ONCE, the
first time anything imports the package (or anything inside it) in a
given process — that makes it the package's front door and its setup
hook.

Structural importance here: models/__init__.py imports every model
module (run, run_pixel, source, ...). Since each model class registers
itself with SQLAlchemy's registry as its module loads, this file
guarantees that touching the package AT ALL loads ALL models — which
is exactly what lets the quoted cross-references ("RunSegment",
"Run.run_number == ...") always resolve. Without it, a script that
imported only `models.run` would crash the first time SQLAlchemy tried
to resolve "RunSegment" from a registry that never saw it. Hence the
docstring's instruction: "Import from here so every mapper is
registered before use."

Utility: it also flattens the import path — callers write
`from calibrationnet.models import Run, RunSegment` instead of one
import per file.

A leaf-package `__init__.py` can be completely empty (many are) — the
file's existence is what matters; content is optional.

## `__all__` (models/__init__.py)

A module-level list of strings naming the module's PUBLIC API. Like
`__tablename__` it is plain data, not a method, and it is a Python
convention (the language reads it in one situation, tools in many).

What Python itself does with it: `from calibrationnet.models import *`
imports exactly the names in `__all__` — without it, star-import would
grab every top-level name, including accidental re-exports (the
imported `Mapped`, `relationship`, etc. would leak out as if they were
models).

What tools do with it (the bigger utility): type checkers, linters,
IDE autocomplete, and documentation generators treat `__all__` as the
module's declared contract — "these 15 names are what this package
offers; everything else is internal." For a package headed to public
release, it IS the public-facing table of contents of the schema.

Maintenance rule: a new model class must be added in BOTH places —
imported at the top and listed in `__all__`.

## `@property` (models/adc_peak.py) — and decorators generally

A line starting with `@` directly above a `def` is a DECORATOR: a
transformer applied to the function at definition time. Think of it as
"wrap or register this function in some machinery." Different
decorators do different things (`@staticmethod`, `@lru_cache`, ...);
the `@` syntax is the common packaging.

`@property` specifically makes a method readable AS IF it were a plain
attribute: with it, `peak.run_pixel` (no parentheses) runs the method
body and returns its result. Without the decorator you would have to
write `peak.run_pixel()`. The point is a computed value that LOOKS
like stored data.

In adc_peak.py the body is `self.spectrum_fit.trap_filter_output
.run_pixel` — a Python-side walk up the relationship chain, three lazy
SQL loads if the objects aren't already in memory. Hence the
docstring's warning: this is a per-object convenience for plotting
loops, NOT a column — it cannot appear in a SQL WHERE clause. To
filter by run/pixel you join the chain in SQL (see queries.py).

Properties are read-only unless a setter is also defined (none here).
Contrast with the `relationship(...)` attributes: those are also
attribute-style access with lazy SQL behind them, but SQLAlchemy knows
their structure and CAN translate them into SQL joins; a @property is
opaque Python that only runs object-by-object.

## Mixins and multiple inheritance (models/spectrum_fit.py, calibration.py)

`class SpectrumFit(CovarianceMixin, Base)` lists TWO parent classes —
multiple inheritance. The class gets everything from both: from `Base`
it becomes a SQLAlchemy model; from `CovarianceMixin` it gains the
`correlations()` method.

A MIXIN is a class designed only to donate behavior — it is not a
model, has no table, and is never instantiated on its own
(covariance.py defines no `__tablename__`, no columns). It works by
assumption: `correlations()` uses `self.var_names` and
`self.covariance`, so any class that stores those two attributes can
mix it in. SpectrumFit and Calibration both do — one implementation
of the correlation math, two tables that share it. That is the whole
point: the alternative would be copy-pasting the method into both
model files, which then drift apart.

Convention: mixins are named `...Mixin` and listed BEFORE the base
class in the parent list.

## `@classmethod` (models/spectrum_fit.py: from_lmfit)

Cousin of `@property`: another decorator changing how a method is
called. A `@classmethod` is called on the CLASS, not on an instance —
`SpectrumFit.from_lmfit(result, ...)` — and receives the class itself
as its first argument (named `cls` by convention, mirroring `self`).

Its classic use, and the use here, is an ALTERNATIVE CONSTRUCTOR: a
named recipe for building an instance from a specific kind of input.
`from_lmfit` translates an lmfit MinimizerResult into a SpectrumFit
row (chi2 from result.chisqr, pars from result.params, ...), ending in
`return cls(...)` — i.e. "construct one of me." Putting it on the
class keeps the lmfit->database mapping in exactly one place; callers
never hand-copy minimizer fields.

The naming pattern `from_<source>` (from_lmfit, and e.g. Python's own
dict.fromkeys, datetime.fromtimestamp) signals this idiom.

### The bare `*` in from_lmfit's signature (vs `*args`)

The `*` symbol does two different jobs in a function signature,
depending on whether it has a name attached. Ground truth first: any
argument can be passed by position or by name —

    def fit(window, width): ...
    fit(400, 3.0)                  # positional: matched by order
    fit(window=400, width=3.0)     # keyword: matched by name

**`*args` — a collector.** A named `*` parameter soaks up any number
of extra positional arguments into a tuple:

    def total(*args):
        return sum(args)
    total(1, 2, 3)                 # args = (1, 2, 3)

**Bare `*` — a fence.** No name attached, and the meaning flips: it
BANS positional arguments from that point on. In

    def from_lmfit(cls, result, *, label=None, config=None): ...

`result` may be positional; everything after the fence must be passed
as `label=...`, `config=...`. A positional attempt fails immediately:

    SpectrumFit.from_lmfit(result, "ce-6peak")
    # TypeError: takes 2 positional arguments but 3 were given

Both behaviors are one rule: `*` marks where positional arguments end
up. With `*args` they land in the tuple; with a bare `*` there is no
bucket, so extras are an error — "a collector with no bucket."

Why from_lmfit wants the fence: four similar-looking optional
settings. Positional calls would depend on memorized parameter order,
and a future reorder would silently shift values into wrong slots.
The fence forces self-documenting calls (`label="ce-6peak"`).

Recognition rule: `*name` = "accepts many positionals";
bare `*` = "accepts no more positionals". (The rarer `/` marker is
the mirror image: parameters before it are positional-only.)

### `-> "SpectrumFit"` — a quoted (forward) reference in a return hint

Same `->` return hint as `-> str`, saying from_lmfit returns a
SpectrumFit instance. The quotes are needed because the `def` line
runs while the SpectrumFit class is still being built — the name is
only bound after the whole class body finishes, so unquoted it would
be a NameError at import:

    class SpectrumFit(CovarianceMixin, Base):
        @classmethod
        def from_lmfit(cls, ...) -> "SpectrumFit":   # name not bound yet
            ...

Quoting makes it a FORWARD REFERENCE that type checkers resolve once
the class exists — the same quoted-name trick as
Mapped[List["RunSegment"]], with a different resolver (type checker
vs SQLAlchemy registry) and reason (mid-definition vs circular
imports). Runtime ignores it either way.

## JSONB columns (models/spectrum_fit.py, covariance storage)

`mapped_column(JSONB)` uses PostgreSQL's binary JSON type: the whole
Python dict or list is stored in ONE database cell — `pars` is a
{name: value} dict, `covariance` a nested list (the matrix). SQLAlchemy
converts Python <-> JSON automatically on write/read.

Why here: fit parameters differ per recipe (a 6-peak CE fit and a
2-peak Auger fit have different parameter sets), so fixed columns per
parameter would need a schema migration every time a recipe changed.
JSONB keeps the schema stable while the payload varies. Trade-offs:
the database cannot enforce structure inside the blob (no constraints
on keys), and querying inside it needs JSON operators — fine for
"store exactly what the minimizer reported" data that is read back
whole. Postgres-specific (imported from sqlalchemy.dialects.postgresql,
like ARRAY in trap_filter_output.py) — this schema deliberately uses
Postgres features rather than staying database-portable.

## Module organization: one file per CONCEPT, not per class

Python does not require (or encourage) one-class-per-file the way
Java does. calibration.py holds both Calibration and CalibrationPoint
because they are one concept — "a fitted curve and the points it was
fit from":

- ownership: points carry cascade="all, delete-orphan" — a point
  cannot outlive its calibration and is never used on its own;
- readability: whoever reads one always needs the other.

Contrast Run vs RunSegment (separate files): a segment has its own
independent life and users. And source.py holds FIVE classes — the
whole interlocking sources-and-line-energies design as one unit.

Rule of thumb: a class earns its own file when it has its own users
and its own story; otherwise it lives with its concept.

## Database key terminology (natural / surrogate / primary / foreign)

Four related but distinct terms, all used deliberately in models/:

- **Primary key**: whichever column(s) uniquely identify a row. Every
  table has one. "Natural" and "surrogate" describe what KIND of
  primary key a table chose:
- **Natural key**: a primary key made of real-world data —
  runs.run_number, pixels.pixel_number. Chosen when the meaningful
  number is itself unique and never reused; saves an id column and
  lets users query by the number they actually know.
- **Surrogate key**: an artificial auto-increment integer primary key
  (run_pixels.id). Chosen when the row's natural identity is a
  multi-column combination (run, segment, pixel) — child tables then
  reference one column instead of repeating three.
- **Foreign key**: different concept — a column that points at
  ANOTHER table's key (run_pixels.pixel_number -> pixels,
  source_id -> sources). A surrogate key is not a foreign key; it is
  what other tables' foreign keys point AT.

The "natural primary key" comments in models/ carry the design
rationale (why no auto-generated id here?), so shortening them to
"primary key" would delete the very fact they exist to record.

## `__table_args__` and CheckConstraint (models/pixel.py, run_pixel.py)

`__table_args__` is another SQLAlchemy-convention dunder (same family
as `__tablename__`): a tuple of TABLE-LEVEL definitions — things that
belong to the table as a whole rather than to one column. Per-column
facts (type, primary key, one-column foreign key, index) ride on
`mapped_column(...)`; anything spanning multiple columns or the whole
table (multi-column constraints, composite foreign keys, table
options) goes in `__table_args__`.

A `CheckConstraint("...sql...", name="ck_...")` is a rule enforced by
POSTGRES ITSELF, not by Python: the SQL expression is stored in the
table definition, and the database evaluates it on every INSERT and
UPDATE from any client — our scripts, a notebook, someone typing raw
psql. If the expression is false the write is rejected with an error
naming the constraint. That is the point: Python-side validation only
protects code paths that remember to validate; a check constraint
protects the data against every writer forever.

The `name=` matters: it is the constraint's permanent name inside
Postgres — it appears in violation error messages (so a name like
`ck_pixels_detector_matches_number` makes the error self-explanatory)
and it is how a migration refers to the constraint when altering or
dropping it. `ck_` is the conventional prefix for check constraints.

Two side notes:
- The SQL is a string, split across source lines using Python's
  implicit string concatenation: two string literals next to each
  other (`"abc " "def"`) fuse into one — there is no comma between
  them, which is exactly what distinguishes this from a tuple.
- Because these live in the database schema, editing one is a
  MIGRATION (development), not cleanup — same rule as columns.

## `if TYPE_CHECKING:` imports (top of every models/ file)

`typing` is a module from Python's standard library (it ships with
Python itself — no install; "standard library" = the batteries
included with the interpreter, imported by name like `datetime` or
`re`). `TYPE_CHECKING` is a constant defined there with a deliberate
split personality:

- **At runtime it is `False`** — so the imports under
  `if TYPE_CHECKING:` never actually execute when the program runs.
- **Static type checkers** (mypy, Pyright — the machinery behind IDE
  autocomplete and red squiggles) **treat it as `True`** — so for them
  the imports exist and the names resolve.

Why import something only for type checkers? **Circular imports.**
The model classes reference each other in both directions (Run has
segments; RunSegment has a run) — if each file imported the other at
runtime, each would require the other to finish loading first, and
Python would fail with an ImportError. This codebase avoids the
problem wholesale: NO model file imports another model at runtime
(verified 2026-08-24 — every cross-model import in models/ sits under
TYPE_CHECKING; the only runtime imports are base.py, the standard
library, and sqlalchemy). Even the foreign key avoids the class:
`ForeignKey("runs.run_number")` names the TABLE and column as a
string, not the Run class.

The runtime half of the trick is that the actual uses are **strings**:
`Mapped[List["RunSegment"]]`, `primaryjoin="Run.run_number == ..."` —
quoted names, not the classes themselves, so Python needs nothing at
import time. SQLAlchemy resolves those strings from its registry of
model classes (every class inheriting from Base registers itself)
once all modules are loaded — and models/__init__.py imports every
module, so importing anything from calibrationnet.models loads them
all.

**What deleting every TYPE_CHECKING block would actually do:**
- Runtime: NOTHING. Every script, fit, ingest, cluster job produces
  byte-identical results. Python never executes those imports anyway.
- What breaks is the tooling's ability to see through the quoted
  annotations. Concretely, in an editor or notebook:
  - `run.segments[0].` stops autocompleting start_time /
    linear_position / ... — the checker no longer knows the list
    holds RunSegment objects;
  - hovering `run.segments` shows an unknown type instead of
    `List[RunSegment]`;
  - a typo like `seg.strat_time` is no longer flagged — it would
    surface as a runtime AttributeError instead of a squiggle;
  - a type checker run over the repo reports "RunSegment is not
    defined" errors in the annotations themselves.

So: the guarded imports buy compile-time-style checking and
navigation for a dynamic language, at zero runtime cost. Delete them
and the code still works; you and your tools just go back to flying
blind through the cross-references.

## Linters (and line-length rules)

A linter is a program that reads source code WITHOUT running it and
flags style and suspicious-pattern issues: unused imports, undefined
names, trailing whitespace, lines longer than a limit. Common Python
ones: flake8 and Ruff (linters), Black (a formatter — it rewrites the
code to a canonical style instead of just complaining). They run from
the terminal or in CI (a check that runs automatically on every push
to the repository), and editors surface their output as squiggles.

The "line-length rule" is the classic example: PEP 8 (Python's style
guide) caps lines at 79 characters; Black's default is 88. The point
is readability in side-by-side diffs/reviews and uniformity across a
codebase — nothing about correctness. A 100-character line runs fine;
a linter would just flag it (flake8 code E501).

Relation to the type checkers met earlier: mypy/Pyright check TYPES
("this can't be a float"), linters check STYLE and hygiene ("this
line is too long, this import is unused"). Both are static tools; a
project can use either, both, or neither. CalibrationNet currently
has no linter configured — line lengths are only a consistency
choice, not an enforced rule.

## Type hints (`-> str`, `Mapped[Optional[float]]`)

Annotations describing what a function returns or a variable holds.
Python itself does not enforce them at runtime — they are documentation
for readers and for tools... with one big exception relevant here:
**SQLAlchemy reads the `Mapped[...]` annotations at class-definition
time to build the schema.** `hv: Mapped[Optional[float]]` is what makes
`hv` a nullable float column; `Optional[X]` means "X or None", which
SQLAlchemy translates to "column may be NULL". So in models/, the type
hints are load-bearing, not just documentation.

`def __repr__(self) -> str:` — the `-> str` says "returns a string";
`self` is the object the method was called on (Python passes it
automatically: `run.__repr__()` means `Run.__repr__(run)`).

## `__init__.py` (why every package directory has one)

The file that marks a directory as a regular Python *package*. Its
presence is what lets `from calibrationnet.acquisition.trap_filter
import ...` resolve as part of the `calibrationnet` package; the file
itself runs (as the module `calibrationnet.acquisition`) the first time
anything under the package is imported. An empty or docstring-only
`__init__.py` is completely normal — its job is to exist, not to hold
code.

Python 3.3+ technically allows packages *without* it ("namespace
packages"), but relying on that is a footgun: package-discovery
tooling (`setuptools.find_packages()`, mypy's default mode, some
linters) skips or mishandles such directories, and any same-named
directory elsewhere on `sys.path` can silently merge into a namespace
package. A regular package (with `__init__.py`) can't be merged into
and reads as an explicit "this is a package" signal. So: keep them,
even when empty.
