"""Regenerates the three made-up syllabi in sample_syllabi/.

    pip install reportlab
    python make_samples.py sample_syllabi
"""
import os
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

out = sys.argv[1] if len(sys.argv) > 1 else "sample_syllabi"
os.makedirs(out, exist_ok=True)
ss = getSampleStyleSheet()
def P(t, s="BodyText"): return Paragraph(t, ss[s])
def doc(name, flow):
    SimpleDocTemplate(f"{out}/{name}", pagesize=letter).build(flow)

# 1: Biology, schedule table
rows = [["Week","Date","Topic","Due"],
 ["8","Oct 13","Cell respiration","Reading: Ch. 9"],
 ["9","Oct 22","Photosynthesis","Lab 4 report due"],
 ["10","Oct 29","Midterm review","Quiz 3 (in class)"],
 ["11","Nov 5","MIDTERM EXAM","Midterm Exam, 25%"],
 ["12","Nov 12","Genetics I","Problem Set 5"],
 ["13","Nov 19","Genetics II","Research poster draft"],
 ["14","Nov 26","No class - Thanksgiving",""],
 ["15","Dec 3","Evolution","Research poster final, 15%"],
 ["16","Dec 10","FINAL EXAM 9:00am","Final Exam, 30%"]]
t = Table(rows); t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.grey),("BACKGROUND",(0,0),(-1,0),colors.lightgrey)]))
doc("BIO101_syllabus.pdf", [P("BIO 101: Introduction to Biology, Fall 2026","Title"),
 P("Instructor: Dr. R. Okafor. Office hours: Tuesdays 2-4pm, Room 210 (no appointment needed)."),
 P("Grading: Labs 20%, Quizzes 10%, Midterm 25%, Research poster 15%, Final 30%. Participation is noted but not graded."),
 Spacer(1,12), P("Course Schedule","Heading2"), t,
 Spacer(1,12), P("All written work is due by 11:59 pm on the date listed unless noted. Late work loses 10% per day.")])

# 2: English, prose
doc("ENG210_syllabus.pdf", [P("ENG 210 - Writing About Film","Title"), P("Fall Semester 2026 · MWF 10:00-10:50"),
 P("Assignments","Heading2"),
 P("<b>Response papers.</b> Short (300 word) responses are due every Friday by 5pm on the course site, starting October 2. These are pass/fail and together are worth 10%."),
 P("<b>Essay 2 (Genre analysis).</b> A 1,500-word essay comparing two films from the same genre. A one-paragraph proposal is due Monday, October 26. The full draft is due Monday, November 9 for peer review, and the final version is due Friday, November 20. Worth 25%."),
 P("<b>Group presentation.</b> In groups of three you will present a 10-minute analysis of a film scene. Presentations happen in class on Wednesday, December 2."),
 P("<b>Final portfolio.</b> Revised versions of both essays plus a reflective letter, due December 14 by noon. Worth 30%."),
 P("Note: there is no class on Wednesday, November 25."),
 P("Essay 1 was due September 25 (already graded).")])

# 3: Math, messy plain lines with numeric dates
doc("MATH152_syllabus.pdf", [P("MATH 152 Calculus II  —  Section 004  —  Fall '26","Title"),
 P("Homework is assigned on WebWork. HW sets close at 11pm on the dates below."),
 P("HW 7: 10/23 &nbsp;&nbsp; HW 8: 10/30 &nbsp;&nbsp; HW 9: 11/6 &nbsp;&nbsp; HW 10: 11/20"),
 P("Quizzes are given in recitation (Thursdays): Quiz 5 on 10/29, Quiz 6 on 11/12."),
 P("Exam 2 covers sections 7.1-8.3 and will be held Tues 11/17, 7-9pm in Hall B."),
 P("The cumulative final exam is Dec 15, 2026 from 1:00-3:00 PM."),
 P("Grade breakdown: HW 15%, Quizzes 10%, Exam 1 20%, Exam 2 20%, Final 35%."),
 P("Exam 1 was Sept 29.")])
