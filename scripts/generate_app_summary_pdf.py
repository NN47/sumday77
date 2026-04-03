from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pdf"
OUTPUT_PATH = OUTPUT_DIR / "my_fitness_bot_summary.pdf"


def bullet_list(items, style, left_indent=12):
    return ListFlowable(
        [ListItem(Paragraph(item, style)) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=left_indent,
        bulletFontName="Helvetica",
        bulletFontSize=6,
        bulletOffsetY=0,
    )


def build_pdf():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=10 * mm,
        title="My Fitness Bot Summary",
        author="OpenAI Codex",
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleCompact",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=22,
            textColor=colors.HexColor("#17324D"),
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Subhead",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#4A6177"),
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHead",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=9.2,
            leading=11,
            textColor=colors.HexColor("#1D6FD6"),
            spaceAfter=3,
            spaceBefore=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyTight",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.3,
            textColor=colors.HexColor("#17324D"),
            alignment=TA_LEFT,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Mini",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9,
            textColor=colors.HexColor("#4A6177"),
        )
    )

    title = [
        Paragraph("My Fitness Bot", styles["TitleCompact"]),
        Paragraph("One-page repo summary generated from source evidence only", styles["Subhead"]),
    ]

    left_story = [
        Paragraph("What It Is", styles["SectionHead"]),
        Paragraph(
            "A Telegram fitness-tracking bot built with aiogram and SQLAlchemy. It helps users log meals, workouts, "
            "water, weight, supplements, procedures, and wellbeing, then adds reminders and AI-assisted analysis.",
            styles["BodyTight"],
        ),
        Spacer(1, 4),
        Paragraph("Who It's For", styles["SectionHead"]),
        Paragraph(
            "Primary persona: a Telegram-first user managing daily fitness and nutrition habits. Repo evidence suggests "
            "a Russian-speaking audience because the bot menus and prompts are written in Russian.",
            styles["BodyTight"],
        ),
        Spacer(1, 4),
        Paragraph("What It Does", styles["SectionHead"]),
        bullet_list(
            [
                "Logs workouts with exercise/category flows, counts, dates, and workout calendar views.",
                "Tracks meals and KBJU with text input, food-photo analysis, and label/barcode-assisted nutrition lookup.",
                "Stores water intake with quick-add buttons and per-day calendar/history views.",
                "Records weight and body measurements over time.",
                "Manages supplement schedules, intake history, and reminder notifications.",
                "Captures wellbeing check-ins and procedures, each with calendar-based review.",
                "Generates day/week/month activity analysis using Gemini plus stored habit data.",
            ],
            styles["BodyTight"],
        ),
    ]

    right_story = [
        Paragraph("How It Works", styles["SectionHead"]),
        Paragraph(
            "<b>Entry/runtime:</b> <font name='Courier'>main.py</font> starts the aiogram bot, in-memory FSM storage, "
            "handler registration, DB init, and a keep-alive HTTP server.",
            styles["BodyTight"],
        ),
        Paragraph(
            "<b>Interaction layer:</b> feature-specific routers live in <font name='Courier'>handlers/</font> for start, meals, "
            "workouts, weight, water, supplements, settings, wellbeing, calendar, procedures, and AI activity analysis.",
            styles["BodyTight"],
        ),
        Paragraph(
            "<b>Persistence:</b> SQLAlchemy models in <font name='Courier'>database/models.py</font> cover users, workouts, meals, "
            "KBJU settings, supplements, procedures, water, wellbeing, measurements, and saved activity analyses; "
            "<font name='Courier'>database/session.py</font> initializes the DB and sessions.",
            styles["BodyTight"],
        ),
        Paragraph(
            "<b>Services:</b> <font name='Courier'>services/gemini_service.py</font> handles Gemini text/vision calls, "
            "<font name='Courier'>services/nutrition_service.py</font> calls CalorieNinjas and Open Food Facts, and "
            "<font name='Courier'>services/notification_scheduler.py</font> sends meal/supplement reminders.",
            styles["BodyTight"],
        ),
        Paragraph(
            "<b>Data flow:</b> Telegram message -> aiogram handler/FSM -> repository or service call -> SQLAlchemy DB/API response -> "
            "formatted bot reply or scheduled reminder.",
            styles["BodyTight"],
        ),
        Spacer(1, 4),
        Paragraph("How To Run", styles["SectionHead"]),
        bullet_list(
            [
                "Install dependencies: <font name='Courier'>pip install -r requirements.txt</font>.",
                "Create env vars or a <font name='Courier'>.env</font> file with <font name='Courier'>API_TOKEN</font>; optional integrations are "
                "<font name='Courier'>GEMINI_API_KEY</font>, <font name='Courier'>GEMINI_API_KEY2</font>, <font name='Courier'>GEMINI_API_KEY3</font>, "
                "<font name='Courier'>NUTRITION_API_KEY</font>, and <font name='Courier'>DATABASE_URL</font>.",
                "Without <font name='Courier'>DATABASE_URL</font>, the app defaults to <font name='Courier'>sqlite:///fitness_bot.db</font>.",
                "Start the bot with <font name='Courier'>python main.py</font>. Docker evidence also exists via "
                "<font name='Courier'>Dockerfile</font> using Python 3.11 and the same entrypoint.",
            ],
            styles["BodyTight"],
        ),
        Spacer(1, 4),
        Paragraph("Not Found In Repo", styles["SectionHead"]),
        bullet_list(
            [
                "Formal product name beyond the bot/repo naming.",
                "Production deployment instructions beyond the minimal Dockerfile.",
                "Explicit target customer segment outside what can be inferred from the UI and features.",
            ],
            styles["BodyTight"],
        ),
    ]

    columns = Table(
        [[left_story, right_story]],
        colWidths=[91 * mm, 83 * mm],
        hAlign="LEFT",
    )
    columns.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#CFD8E3")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LINEBEFORE", (1, 0), (1, 0), 0.5, colors.HexColor("#CFD8E3")),
            ]
        )
    )

    footer = Paragraph(
        "Evidence sources used: <font name='Courier'>main.py</font>, <font name='Courier'>config.py</font>, "
        "<font name='Courier'>requirements.txt</font>, <font name='Courier'>Dockerfile</font>, "
        "<font name='Courier'>database/models.py</font>, <font name='Courier'>database/session.py</font>, "
        "<font name='Courier'>services/*.py</font>, <font name='Courier'>handlers/*.py</font>, and "
        "<font name='Courier'>utils/keyboards.py</font>.",
        styles["Mini"],
    )

    story = title + [columns, Spacer(1, 6), footer]
    doc.build(story)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = build_pdf()
    print(path)
