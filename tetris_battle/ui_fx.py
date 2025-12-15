"""Small pygame UI helpers for Tetris Battle."""
import random, math, pygame


def draw_header(surface, text, sub="", font=None, sub_font=None, pos=(20, 20)):
    font = font or pygame.font.SysFont(None, 32)
    sub_font = sub_font or pygame.font.SysFont(None, 22)
    surface.blit(font.render(text, True, (230, 230, 230)), pos)
    if sub:
        surface.blit(sub_font.render(sub, True, (190, 190, 190)), (pos[0], pos[1] + 30))


def draw_block_cell(surface, x, y, size, color):
    rect = pygame.Rect(x, y, size, size)
    pygame.draw.rect(surface, color, rect, border_radius=4)
    pygame.draw.rect(surface, (0, 0, 0), rect, width=2, border_radius=4)


class FlashOverlay:
    def __init__(self, color=(255, 255, 255), duration=0.25):
        self.color = color
        self.duration = duration
        self.t = 0.0

    def trigger(self):
        self.t = self.duration

    def draw(self, surface, rect):
        if self.t <= 0:
            return
        alpha = int(180 * (self.t / self.duration))
        overlay = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        overlay.fill((*self.color, alpha))
        surface.blit(overlay, rect)
        self.t -= 0.016


class ScreenShake:
    def __init__(self):
        self.strength = 0

    def trigger(self, amount=6):
        self.strength = max(self.strength, amount)

    def offset(self):
        if self.strength <= 0:
            return 0, 0
        self.strength *= 0.9
        return (random.randint(-self.strength, self.strength),
                random.randint(-self.strength, self.strength))


class Confetti:
    def __init__(self):
        self.particles = []

    def burst(self, pos, n=20):
        for _ in range(n):
            ang = random.random() * math.tau
            speed = random.uniform(80, 160)
            self.particles.append({
                "x": pos[0], "y": pos[1],
                "vx": math.cos(ang) * speed,
                "vy": math.sin(ang) * speed,
                "life": random.uniform(0.5, 1.2),
                "c": random.choice([(0,255,180),(255,200,0),(255,80,120),(80,160,255)])
            })

    def update_draw(self, surface, dt):
        alive = []
        for p in self.particles:
            p["life"] -= dt
            if p["life"] <= 0:
                continue
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            pygame.draw.circle(surface, p["c"], (int(p["x"]), int(p["y"])), 3)
            alive.append(p)
        self.particles = alive
