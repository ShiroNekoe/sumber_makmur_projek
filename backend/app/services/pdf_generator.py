import io
import os
import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, and_

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

from app.domain.models import ClosedTrade, OpenPosition
from app.infrastructure.database.models import ClosedTradeORM, OpenPositionORM

logger = logging.getLogger(__name__)

def make_summary_cell(label, value, is_pnl=False):
    styles = getSampleStyleSheet()
    color_str = "white"
    if is_pnl:
        try:
            val_num = float(value.replace("$", "").replace("%", "").replace("+", ""))
            if val_num > 0:
                color_str = "#10B981" # green
            elif val_num < 0:
                color_str = "#F43F5E" # red
        except Exception:
            pass
            
    cell_html = f"""
    <para align="center">
        <font size="8" color="#94A3B8" face="Helvetica-Bold">{label.upper()}</font><br/><br/>
        <font size="14" color="{color_str}" face="Helvetica-Bold">{value}</font>
    </para>
    """
    return Paragraph(cell_html, styles["Normal"])

async def generate_portfolio_pdf(
    db_session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> bytes:
    """
    Generates a premium transaction and portfolio report in PDF format.
    Filters transactions in the database using start_date and end_date if provided.
    """
    # 1. Fetch Closed Trades
    query_closed = select(ClosedTradeORM)
    filters = []
    if start_date:
        if start_date.tzinfo is not None:
            start_date = start_date.replace(tzinfo=None)
        filters.append(ClosedTradeORM.exit_ts >= start_date)
    if end_date:
        if end_date.tzinfo is not None:
            end_date = end_date.replace(tzinfo=None)
        filters.append(ClosedTradeORM.exit_ts <= end_date)
        
    if filters:
        query_closed = query_closed.where(and_(*filters))
        
    query_closed = query_closed.order_by(ClosedTradeORM.exit_ts.desc())
    closed_res = db_session.execute(query_closed).scalars().all()
    
    # 2. Fetch Open Positions
    query_open = select(OpenPositionORM).order_by(OpenPositionORM.entry_ts.desc())
    open_res = db_session.execute(query_open).scalars().all()
    
    # Calculate performance metrics
    total_trades = len(closed_res)
    realized_pnl = 0.0
    wins = 0
    total_r = 0.0
    total_holding_time = 0.0
    
    for t in closed_res:
        realized_pnl += (t.position_size_usd * t.pnl_pct_actual)
        if t.label == "BUY_BENAR":
            wins += 1
        total_r += t.r_multiple
        total_holding_time += t.holding_time_minutes
        
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    avg_r = (total_r / total_trades) if total_trades > 0 else 0.0
    avg_holding = (total_holding_time / total_trades) if total_trades > 0 else 0.0
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    styles = getSampleStyleSheet()
    
    # Define custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#FFFFFF"),
        fontName='Helvetica-Bold'
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#6366F1"), # Indigo Accent
        fontName='Helvetica-Bold'
    )
    
    section_title_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#F1F5F9"),
        fontName='Helvetica-Bold'
    )
    
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#94A3B8"),
        fontName='Helvetica'
    )
    
    th_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#94A3B8"),
        fontName='Helvetica-Bold'
    )
    
    td_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#E2E8F0"),
        fontName='Helvetica'
    )
    
    td_bold_style = ParagraphStyle(
        'TableCellBold',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#FFFFFF"),
        fontName='Helvetica-Bold'
    )

    story = []
    
    # Draw Background Decoration & Header
    header_data = [
        [
            Paragraph("SUMBER MAKMUR HYPE", title_style),
            Paragraph(f"DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC", ParagraphStyle('RightHeader', parent=body_style, alignment=2))
        ],
        [
            Paragraph("AI SMART MONEY TRADING SYSTEM • PORTFOLIO REPORT", subtitle_style),
            Paragraph(f"RANGE: {start_date.strftime('%Y-%m-%d') if start_date else 'ALL'} to {end_date.strftime('%Y-%m-%d') if end_date else 'NOW'}", ParagraphStyle('RightHeader2', parent=body_style, alignment=2))
        ]
    ]
    
    header_table = Table(header_data, colWidths=[4.0*inch, 3.5*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    
    story.append(header_table)
    story.append(Spacer(1, 15))
    
    # Executive Summary Card Blocks
    summary_data = [
        [
            make_summary_cell("Total Closed Trades", str(total_trades)),
            make_summary_cell("Realized PnL", f"${realized_pnl:+.2f}", is_pnl=True),
            make_summary_cell("Win Rate", f"{win_rate:.1f}%"),
            make_summary_cell("Average R-Multiple", f"{avg_r:+.2f}R", is_pnl=True),
            make_summary_cell("Avg Holding Time", f"{avg_holding:.1f}m")
        ]
    ]
    
    summary_table = Table(summary_data, colWidths=[1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#0F172A")), # Dark Slate Card
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#1E293B")),
        ('INNERGRID', (0,0), (-1,-1), 1, colors.HexColor("#1E293B")),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    
    story.append(summary_table)
    story.append(Spacer(1, 20))
    
    # Section: Closed Transactions
    story.append(Paragraph("CLOSED TRANSACTION LOGS", section_title_style))
    story.append(Spacer(1, 5))
    
    # Table Header for Closed Trades
    closed_headers = [
        Paragraph("ID", th_style),
        Paragraph("ASSET", th_style),
        Paragraph("DIRECTION", th_style),
        Paragraph("SIZE (USD)", th_style),
        Paragraph("HOLD TIME", th_style),
        Paragraph("PNL (%)", th_style),
        Paragraph("R-MULTIPLE", th_style),
        Paragraph("EXIT REASON", th_style)
    ]
    
    closed_rows = [closed_headers]
    
    for t in closed_res:
        pnl_val = t.pnl_pct_actual * 100
        pnl_color = "#10B981" if pnl_val >= 0 else "#F43F5E"
        r_color = "#10B981" if t.r_multiple >= 0 else "#F43F5E"
        
        row = [
            Paragraph(t.trade_id, td_style),
            Paragraph(f"<b>{t.token_symbol}</b><br/><font color='#64748B'>{t.token_address[:6]}...{t.token_address[-4:]}</font>", td_style),
            Paragraph(t.direction, td_bold_style),
            Paragraph(f"${t.position_size_usd:.2f}", td_style),
            Paragraph(f"{t.holding_time_minutes}m", td_style),
            Paragraph(f"<font color='{pnl_color}'><b>{pnl_val:+.2f}%</b></font>", td_style),
            Paragraph(f"<font color='{r_color}'><b>{t.r_multiple:+.2f}R</b></font>", td_style),
            Paragraph(t.exit_reason, td_style)
        ]
        closed_rows.append(row)
        
    if len(closed_rows) == 1:
        closed_rows.append([
            Paragraph("No closed transactions recorded in database for the selected range.", td_style),
            "", "", "", "", "", "", ""
        ])
        
    closed_table = Table(closed_rows, colWidths=[1.0*inch, 1.3*inch, 0.8*inch, 0.9*inch, 0.8*inch, 0.9*inch, 0.9*inch, 1.1*inch])
    closed_table_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E293B")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#334155")),
    ]
    if len(closed_rows) == 2 and closed_rows[1][1] == "":
        closed_table_style.append(('SPAN', (0,1), (-1,1)))
        closed_table_style.append(('ALIGN', (0,1), (-1,1), 'CENTER'))
        
    closed_table.setStyle(TableStyle(closed_table_style))
    story.append(closed_table)
    story.append(Spacer(1, 20))
    
    # Section: Active Positions
    story.append(Paragraph("CURRENT OPEN POSITIONS", section_title_style))
    story.append(Spacer(1, 5))
    
    open_headers = [
        Paragraph("ID", th_style),
        Paragraph("TOKEN ACCOUNT", th_style),
        Paragraph("STATE", th_style),
        Paragraph("SIZE (USD)", th_style),
        Paragraph("ENTRY TIME", th_style),
        Paragraph("ENTRY PRICE", th_style),
        Paragraph("INITIAL SL", th_style),
        Paragraph("CONFIDENCE", th_style)
    ]
    
    open_rows = [open_headers]
    for p in open_res:
        row = [
            Paragraph(p.position_id, td_style),
            Paragraph(f"<b>{p.token_address[:6]}...{p.token_address[-4:]}</b>", td_bold_style),
            Paragraph(p.state, td_bold_style),
            Paragraph(f"${p.position_size_usd:.2f}", td_style),
            Paragraph(p.entry_ts.strftime('%H:%M:%S UTC') if p.entry_ts else 'N/A', td_style),
            Paragraph(f"${p.entry_price:.6f}" if p.entry_price else 'N/A', td_style),
            Paragraph(f"${p.sl_initial:.6f}", td_style),
            Paragraph(f"{p.confidence_score * 100:.1f}%", td_style)
        ]
        open_rows.append(row)
        
    if len(open_rows) == 1:
        open_rows.append([
            Paragraph("No active open positions running.", td_style),
            "", "", "", "", "", "", ""
        ])
        
    open_table = Table(open_rows, colWidths=[1.0*inch, 1.3*inch, 0.8*inch, 0.9*inch, 1.0*inch, 0.9*inch, 0.9*inch, 0.9*inch])
    open_table_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1E293B")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor("#334155")),
    ]
    if len(open_rows) == 2 and open_rows[1][1] == "":
        open_table_style.append(('SPAN', (0,1), (-1,1)))
        open_table_style.append(('ALIGN', (0,1), (-1,1), 'CENTER'))
        
    open_table.setStyle(TableStyle(open_table_style))
    story.append(open_table)
    
    # Document background page template (Premium Cyber theme)
    def draw_bg(canvas, doc):
        canvas.saveState()
        # Draw Dark Background (#020617)
        canvas.setFillColor(colors.HexColor("#020617"))
        canvas.rect(0, 0, doc.pagesize[0], doc.pagesize[1], fill=1, stroke=0)
        # Header accent bar (Indigo)
        canvas.setFillColor(colors.HexColor("#4F46E5"))
        canvas.rect(36, doc.pagesize[1] - 40, doc.pagesize[0] - 72, 3, fill=1, stroke=0)
        
        # Footer
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(36, 20, "Sumber Makmur Trading Engine - Confidential Report")
        canvas.drawRightString(doc.pagesize[0] - 36, 20, f"Page {doc.page}")
        canvas.restoreState()

    # Build the document
    doc.build(story, onFirstPage=draw_bg, onLaterPages=draw_bg)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
