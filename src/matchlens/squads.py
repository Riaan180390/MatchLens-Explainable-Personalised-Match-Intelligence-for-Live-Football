"""Fictional clubs and players. Nothing here refers to a real club or person."""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import PlayerInfo, TeamInfo

# Base shape per formation slot: (role, x, y) in the team's attacking frame with the ball near halfway.
FORMATIONS: dict[str, list[tuple[str, float, float]]] = {
    "4-3-3": [
        ("GK", 5, 34), ("RB", 32, 8), ("CB", 24, 24), ("CB", 24, 44), ("LB", 32, 60),
        ("DM", 42, 34), ("CM", 54, 20), ("CM", 54, 48),
        ("RW", 74, 10), ("ST", 82, 34), ("LW", 74, 58),
    ],
    "4-2-3-1": [
        ("GK", 5, 34), ("RB", 32, 8), ("CB", 24, 24), ("CB", 24, 44), ("LB", 32, 60),
        ("DM", 44, 24), ("DM", 44, 44), ("AM", 64, 34),
        ("RW", 72, 10), ("ST", 84, 34), ("LW", 72, 58),
    ],
}


@dataclass
class PlayerSpec:
    player_id: str
    name: str
    number: int
    position: str
    passing: int
    dribbling: int
    finishing: int
    tackling: int
    pace_kmh: float
    work_rate: float = 1.0
    goalkeeping: int = 0

    @property
    def short_name(self) -> str:
        return self.name.split(" ", 1)[-1]

    def info(self) -> PlayerInfo:
        return PlayerInfo(
            player_id=self.player_id, name=self.name, short_name=self.short_name,
            number=self.number, position=self.position,
        )


@dataclass
class TeamSpec:
    team_id: str
    name: str
    short_name: str
    primary_color: str
    secondary_color: str
    formation: str
    possession: float  # 0..1 preference for keeping the ball
    directness: float  # 0..1 preference for forward/long passes
    press: float  # 0..1 pressing intensity out of possession
    starters: list[PlayerSpec]
    bench: list[PlayerSpec] = field(default_factory=list)

    def info(self) -> TeamInfo:
        return TeamInfo(
            team_id=self.team_id, name=self.name, short_name=self.short_name,
            primary_color=self.primary_color, secondary_color=self.secondary_color,
            formation=self.formation,
            starting_xi=[p.info() for p in self.starters],
            bench=[p.info() for p in self.bench],
        )


def _p(team: str, number: int, name: str, position: str, passing: int, dribbling: int,
       finishing: int, tackling: int, pace: float, work: float = 1.0, gk: int = 0) -> PlayerSpec:
    return PlayerSpec(f"{team}-{number:02d}", name, number, position, passing, dribbling,
                      finishing, tackling, pace, work, gk)


def kestrel_bay() -> TeamSpec:
    t = "KBY"
    return TeamSpec(
        team_id=t, name="Kestrel Bay FC", short_name="KBY",
        primary_color="#0E7C86", secondary_color="#F4F1EA", formation="4-3-3",
        possession=0.72, directness=0.32, press=0.55,
        starters=[
            _p(t, 1, "Elias Brandvold", "GK", 68, 30, 10, 20, 27.5, 0.9, gk=82),
            _p(t, 2, "Dami Okonkwo-Reyes", "RB", 76, 72, 45, 74, 34.1, 1.08),
            _p(t, 5, "Callum Thwaite", "CB", 72, 50, 35, 83, 31.0, 0.98),
            _p(t, 6, "Mateus Ferrandi", "CB", 80, 58, 38, 80, 31.8, 0.97),
            _p(t, 3, "Jonah Pell", "LB", 74, 70, 40, 72, 33.6, 1.05),
            _p(t, 4, "Sid Abernethy", "DM", 84, 64, 52, 81, 30.2, 1.10),
            _p(t, 8, "Luca Vantaggio", "CM", 83, 76, 66, 66, 31.5, 1.06),
            _p(t, 10, "Teodor Halvik", "CM", 92, 84, 74, 55, 31.2, 1.02),
            _p(t, 7, "Amari Quist", "RW", 76, 88, 76, 38, 36.2, 1.04),
            _p(t, 9, "Rafe Delacroix", "ST", 70, 78, 86, 35, 33.4, 0.98),
            _p(t, 11, "Nico Saltmarsh", "LW", 77, 83, 74, 40, 34.6, 1.03),
        ],
        bench=[
            _p(t, 13, "Arlo Penhaligon", "GK", 60, 25, 10, 20, 27.0, 0.9, gk=74),
            _p(t, 14, "Marcus Oyelaran", "CB", 70, 52, 30, 78, 32.0, 1.0),
            _p(t, 15, "Ivo Strand", "LB", 71, 66, 38, 70, 33.0, 1.04),
            _p(t, 16, "Kian Rowntree", "CM", 79, 70, 60, 68, 31.0, 1.08),
            _p(t, 17, "Bastian Ekwueme", "CM", 77, 78, 68, 58, 32.5, 1.05),
            _p(t, 18, "Yusuf Al-Rashidi", "RW", 72, 84, 72, 36, 35.4, 1.02),
            _p(t, 19, "Dex Coldwater", "ST", 66, 70, 82, 34, 33.0, 1.0),
        ],
    )


def ironvale_athletic() -> TeamSpec:
    t = "IRV"
    return TeamSpec(
        team_id=t, name="Ironvale Athletic", short_name="IRV",
        primary_color="#B22234", secondary_color="#2B2B2B", formation="4-2-3-1",
        possession=0.42, directness=0.66, press=0.78,
        starters=[
            _p(t, 1, "Hamish Corrigan", "GK", 62, 28, 10, 22, 27.0, 0.9, gk=80),
            _p(t, 2, "Tobi Adeyemi-Clarke", "RB", 70, 70, 42, 76, 34.8, 1.09),
            _p(t, 4, "Viktor Haldane", "CB", 68, 48, 40, 88, 31.4, 1.0),
            _p(t, 5, "Sol Navarro-Pike", "CB", 71, 52, 36, 81, 32.2, 0.98),
            _p(t, 3, "Ezra Montclair", "LB", 69, 68, 40, 75, 33.9, 1.06),
            _p(t, 6, "Brody Kettleborough", "DM", 74, 58, 50, 86, 30.5, 1.12),
            _p(t, 8, "Joaquin Arrabal", "DM", 80, 70, 60, 74, 30.8, 1.08),
            _p(t, 10, "Leon Achterberg", "AM", 86, 82, 76, 48, 31.6, 1.03),
            _p(t, 7, "Kai Thornbury", "RW", 72, 82, 72, 42, 35.6, 1.07),
            _p(t, 9, "Zane Mbeki-Foster", "ST", 64, 70, 88, 40, 32.6, 1.0),
            _p(t, 11, "Felix Marangoni", "LW", 73, 85, 75, 40, 35.0, 1.04),
        ],
        bench=[
            _p(t, 12, "Owen Laskaris", "GK", 58, 24, 10, 20, 26.8, 0.9, gk=72),
            _p(t, 14, "Ricky Stellan", "CB", 66, 46, 32, 79, 31.0, 1.0),
            _p(t, 15, "Ade Bankole-Shaw", "RB", 68, 66, 40, 72, 34.0, 1.05),
            _p(t, 16, "Moritz Quell", "DM", 76, 62, 55, 78, 30.6, 1.1),
            _p(t, 17, "Ty Ballantyne", "AM", 80, 80, 70, 50, 32.0, 1.04),
            _p(t, 18, "Rocco Fenwick", "LW", 70, 82, 72, 38, 35.2, 1.03),
            _p(t, 19, "Jalen Okoro-Vance", "ST", 62, 72, 84, 36, 34.0, 1.0),
        ],
    )


CLUBS = {"KBY": kestrel_bay, "IRV": ironvale_athletic}
