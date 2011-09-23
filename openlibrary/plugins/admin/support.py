import datetime
import textwrap

import web
from infogami.utils.view import render_template, add_flash_message
from infogami import config

from openlibrary.core import support

support_db = None

class cases(object):
    def GET(self, typ = "new"):
        current_user = web.ctx.site.get_user()
        if not support_db:
            return render_template("admin/cases", None, None, True, None, False)
        i = web.input(sort="status", desc = "true", all = "false")
        sortby = i['sort']
        desc = i['desc']
        cases = support_db.get_all_cases(typ, summarise = False, sortby = sortby, desc = desc)
        if i['all'] == "false":
            cases = (x for x in cases if x.assignee == current_user.get_email())
            summary = support_db.get_all_cases(typ, summarise = True, user = current_user.get_email())
        else:
            summary = support_db.get_all_cases(typ, summarise = True)
        total = sum(int(x) for x in summary.values())
        desc = desc == "false" and "true" or "false"
        return render_template("admin/cases", summary, total, cases, desc, current_user)
    POST = GET
    

class case(object):
    def GET(self, caseid):
        user = web.ctx.site.get_user()
        if not support_db:
            return render_template("admin/cases", None, None, True, False)
        case = support_db.get_case(caseid)
        admins = [(x.get_email(), x.get_name(), x.get_email() == case.assignee) for x in web.ctx.site.get("/usergroup/admin").members]
        case.update_message_count(user.get_email())
        return render_template("admin/case", case, admins)

    def POST(self, caseid):
        if not support_db:
            return render_template("admin/cases", None, None, True, False)
        user = web.ctx.site.get_user()
        by = user.get_email()
        case = support_db.get_case(caseid)
        assignee = case.assignee
        form = web.input(copycreator = False,
                         assignee = case.assignee,
                         closecase = False,
                         casenote = "",
                         email = case.creator_email,
                         button = False)
        if form["button"]:
            {"REASSIGN": self.POST_reassign,
             "CLOSE CASE": self.POST_closecase,
             "OPEN CASE": self.POST_opencase}[form["button"]](form, case)
        else:
            # TBD This needs to be cleaned up
            summary = "commented"
            casenote = form['casenote']
            if form["copycreator"]:
                summary = "replied"
                if assignee != user.get_email(): # Automatic reassign if updated by someone else. 
                    case.reassign(user.get_email())
                email_to = form.get("email", False)
                subject = "Case #%s: %s"%(case.caseno, case.subject)
                if email_to:
                    message = render_template("admin/email", case, casenote)
                    web.sendmail(config.get("support_case_control_address","support@openlibrary.org"), email_to, subject, message)
            if form["assignee"] != case.assignee: # Reassignments
                summary = "reassigned case to '%s'"%form.get("assignee","")
                case.reassign(form["assignee"])
                subject = "Case #%s has been assigned to you"%case.caseno
                message = render_template("admin/email_reassign", case, '')
                web.sendmail(config.get("support_case_control_address","support@openlibrary.org"), assignee, subject, message)
                add_flash_message("info", "Case reassigned")
            if form["closecase"]: # Closing cases
                summary = "closed the case"
                case.change_status("closed")
                add_flash_message("info", "Case closed")
            case.add_worklog_entry(by = by,
                                   text = casenote,
                                   summary = summary)
        admins = [(x.get_email(), x.get_name(), x.get_email() == case.assignee) for x in web.ctx.site.get("/usergroup/admin").members]
        return render_template("admin/case", case, admins)

    def POST_reassign(self, form, case):
        user = web.ctx.site.get_user()
        assignee = form.get("assignee", False)
        if assignee != case.assignee:
            case.reassign(assignee)
            subject = "Case #%s has been assigned to you"%case.caseno
            message = render_template("admin/email_reassign", case, '')
            web.sendmail(config.get("support_case_control_address","support@openlibrary.org"), assignee, subject, message)
            add_flash_message("info", "Case reassigned")
            case.add_worklog_entry(by = user.get_email(),
                                   text =  "",
                                   summary = "reassigned case to '%s'"%form.get("assignee",""))

    def POST_opencase(self, form, case):
        user = web.ctx.site.get_user()
        by = user.get_email()
        case.add_worklog_entry(by = by,
                               text = '',
                               summary = "opened the case")
        case.change_status("new")
        add_flash_message("info", "Case reopened")

    def POST_closecase(self, form, case):
        user = web.ctx.site.get_user()
        by = user.get_email()
        case.add_worklog_entry(by = by,
                               text = '',
                               summary = "closed the case")
        case.change_status("closed")
        add_flash_message("info", "Case closed")


def setup():
    global support_db
    try:
        support_db = support.Support()
    except support.DatabaseConnectionError:
        support_db = None



