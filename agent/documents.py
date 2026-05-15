# agent/documents.py — Génération de documents PDF

import os
from datetime import datetime
from fpdf import FPDF

DOCUMENTS_DIR = "documents"


def _ensure_dir():
    os.makedirs(DOCUMENTS_DIR, exist_ok=True)


class _QuittancePDF(FPDF):

    def __init__(self):
        super().__init__()
        self.core_fonts_encoding = "windows-1252"

    def header(self):
        # Bande de couleur en haut
        self.set_fill_color(37, 99, 235)
        self.rect(0, 0, 210, 18, "F")
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(255, 255, 255)
        self.set_xy(0, 4)
        self.cell(0, 10, "Christophe.AI  ·  Gestion immobilière", align="C")
        self.set_text_color(0, 0, 0)
        self.ln(16)

    def footer(self):
        self.set_y(-18)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 6, "Document généré automatiquement par Christophe.AI — agent immobilier", align="C")


def generer_quittance(donnees: dict) -> str:
    """
    Génère une quittance de loyer PDF.

    donnees attendues (Claude les remplit depuis le system prompt) :
      tenant_name      : "Thomas Martin"
      landlord_name    : "Marie Dubois"
      property_address : "42 rue de la Roquette, 75011 Paris"
      periode          : "juillet 2025"
      date_debut       : "01/07/2025"
      date_fin         : "31/07/2025"
      loyer_hc         : 950
      charges          : 80
      total            : 1030
      date_emission    : "15/05/2026"   (optionnel, défaut = aujourd'hui)
    """
    _ensure_dir()

    tenant   = donnees.get("tenant_name", "Thomas Martin")
    landlord = donnees.get("landlord_name", "Marie Dubois")
    adresse  = donnees.get("property_address", "42 rue de la Roquette, 75011 Paris")
    periode  = donnees.get("periode", "")
    debut    = donnees.get("date_debut", "")
    fin      = donnees.get("date_fin", "")
    loyer_hc = float(donnees.get("loyer_hc", 0))
    charges  = float(donnees.get("charges", 0))
    total    = float(donnees.get("total", loyer_hc + charges))
    emis_le  = donnees.get("date_emission", datetime.today().strftime("%d/%m/%Y"))

    pdf = _QuittancePDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(20, 25, 20)

    # ── Titre ────────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(37, 99, 235)
    pdf.ln(4)
    pdf.cell(0, 12, "QUITTANCE DE LOYER", align="C", ln=True)
    pdf.set_text_color(100, 100, 100)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Période : {periode}" if periode else "", align="C", ln=True)
    pdf.ln(6)

    # ── Séparateur ────────────────────────────────────────────────────────────
    pdf.set_draw_color(220, 220, 220)
    pdf.set_line_width(0.4)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(6)

    # ── Parties ───────────────────────────────────────────────────────────────
    def section(titre, lignes):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 5, titre.upper(), ln=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(20, 20, 20)
        for l in lignes:
            pdf.cell(0, 7, l, ln=True)
        pdf.ln(3)

    col_w = 80
    y_start = pdf.get_y()

    # Bailleur (gauche)
    pdf.set_xy(20, y_start)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(col_w, 5, "BAILLEUR", ln=False)

    # Locataire (droite)
    pdf.set_xy(110, y_start)
    pdf.cell(col_w, 5, "LOCATAIRE", ln=True)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(20, 20, 20)
    pdf.set_xy(20, pdf.get_y())
    pdf.cell(col_w, 7, landlord, ln=False)
    pdf.set_xy(110, pdf.get_y())
    pdf.cell(col_w, 7, tenant, ln=True)
    pdf.ln(4)

    # ── Bien loué ─────────────────────────────────────────────────────────────
    pdf.set_fill_color(245, 247, 255)
    pdf.set_draw_color(200, 210, 250)
    pdf.set_line_width(0.3)
    pdf.rect(20, pdf.get_y(), 170, 14, "DF")
    pdf.set_xy(24, pdf.get_y() + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(80, 80, 160)
    pdf.cell(30, 5, "Bien loué :", ln=False)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 5, adresse, ln=True)
    pdf.ln(8)

    # ── Corps légal ───────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)
    periode_str = f"du {debut} au {fin}" if debut and fin else f"de {periode}" if periode else ""
    corps = (
        f"Je soussigné(e) {landlord}, bailleur du logement désigné ci-dessus, "
        f"donne quittance à {tenant} du paiement de la somme de "
        f"{total:.2f} € pour le loyer {periode_str}."
    )
    pdf.multi_cell(0, 7, corps)
    pdf.ln(6)

    # ── Tableau des montants ──────────────────────────────────────────────────
    def ligne_montant(label, montant, bold=False, bg=None):
        if bg:
            pdf.set_fill_color(*bg)
            pdf.rect(20, pdf.get_y(), 170, 9, "F")
        pdf.set_xy(24, pdf.get_y())
        style = "B" if bold else ""
        pdf.set_font("Helvetica", style, 11)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(130, 9, label, ln=False)
        pdf.set_font("Helvetica", style, 11)
        pdf.cell(36, 9, f"{montant:.2f} €", align="R", ln=True)

    pdf.set_draw_color(200, 210, 250)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    ligne_montant("Loyer hors charges", loyer_hc, bg=(248, 248, 255))
    ligne_montant("Provision sur charges", charges, bg=(252, 252, 255))
    pdf.set_draw_color(37, 99, 235)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    ligne_montant("TOTAL réglé", total, bold=True, bg=(235, 242, 255))
    pdf.set_draw_color(220, 220, 220)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(10)

    # ── Signature ─────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Émis le {emis_le}", ln=True)
    pdf.ln(12)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(85, 6, f"Signature du bailleur ({landlord})", ln=False)
    pdf.cell(85, 6, f"Signature du locataire ({tenant})", align="R", ln=True)
    pdf.ln(3)
    pdf.set_draw_color(150, 150, 150)
    pdf.line(20, pdf.get_y() + 14, 90, pdf.get_y() + 14)
    pdf.line(110, pdf.get_y() + 14, 190, pdf.get_y() + 14)

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    safe_periode = periode.replace(" ", "_").replace("/", "-") if periode else "document"
    filename = f"quittance_{safe_periode}.pdf"
    filepath = os.path.join(DOCUMENTS_DIR, filename)
    pdf.output(filepath)
    return filepath
