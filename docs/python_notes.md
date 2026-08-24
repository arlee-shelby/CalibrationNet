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
