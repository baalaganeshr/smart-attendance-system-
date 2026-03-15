import os
import re
from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_image(doc, img_path):
    if os.path.exists(img_path):
        try:
            doc.add_picture(img_path, width=Inches(6))
            last_paragraph = doc.paragraphs[-1]
            last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception as e:
            print(f"Error adding image {img_path}: {e}")
            doc.add_paragraph(f"[Image Error: {img_path}]")
    else:
        doc.add_paragraph(f"[Image Missing: {img_path}]")

def process_element(element, doc):
    if isinstance(element, NavigableString):
        text = str(element).strip()
        if text:
            doc.add_paragraph(text)
        return

    if element.name in ['h1', 'h2', 'h3']:
        level = int(element.name[1])
        text = element.get_text().strip()
        if text:
            p = doc.add_heading(text, level=level)
            if level == 1:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in p.runs:
                    run.font.color.rgb = RGBColor(0, 0, 0)
                    run.font.name = 'Times New Roman'
                    run.font.bold = True

    elif element.name == 'p':
        text = element.get_text().strip()
        if text:
            p = doc.add_paragraph(text)
            if 'text-align: center' in str(element.get('style', '')):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    elif element.name == 'ul':
        for li in element.find_all('li', recursive=False):
            p = doc.add_paragraph(li.get_text().strip(), style='List Bullet')

    elif element.name == 'ol':
        for li in element.find_all('li', recursive=False):
            p = doc.add_paragraph(li.get_text().strip(), style='List Number')

    elif element.name == 'div':
        classes = element.get('class', [])
        if 'page-break' in classes:
            doc.add_page_break()
        elif 'code-block' in classes:
            text = element.get_text()
            p = doc.add_paragraph()
            p.style = 'No Spacing' # Use a style with less spacing if available
            run = p.add_run(text)
            run.font.name = 'Courier New'
            run.font.size = Pt(9)
            # Add a slight border visually by using a new paragraph style or just formatting
            # Keep it simple for now
        else:
            # Recursive processing for generic divs
            for child in element.children:
                if isinstance(child, Tag):
                    process_element(child, doc)
                elif isinstance(child, NavigableString):
                    t = str(child).strip()
                    if t: doc.add_paragraph(t)

    elif element.name == 'table':
        rows = element.find_all('tr')
        if not rows: return
        
        # Calculate max columns
        cols = 0
        for row in rows:
            cols = max(cols, len(row.find_all(['td', 'th'])))
        
        if cols > 0:
            table = doc.add_table(rows=len(rows), cols=cols)
            table.style = 'Table Grid'
            
            for i, row in enumerate(rows):
                cells = row.find_all(['td', 'th'])
                for j, cell in enumerate(cells):
                    if j < cols:
                        table.cell(i, j).text = cell.get_text().strip()

    elif element.name == 'img':
        src = element.get('src')
        if src:
            # Assuming images are relative to the HTML file
            img_path = os.path.abspath(src)
            add_image(doc, img_path)

    # Allow custom recursively scanning if needed, but the main loop handles top-level well.

def create_word_doc(html_file, output_file):
    if not os.path.exists(html_file):
        print(f"Error: HTML file '{html_file}' not found.")
        return

    with open(html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')
    doc = Document()
    
    # Set default font
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)

    # Process only direct children of body to maintain order
    for element in soup.body.children:
        if isinstance(element, Tag):
            process_element(element, doc)
    
    doc.save(output_file)
    print(f"Successfully created '{output_file}'")

if __name__ == "__main__":
    create_word_doc("project_report.html", "Project_Report_Final.docx")
