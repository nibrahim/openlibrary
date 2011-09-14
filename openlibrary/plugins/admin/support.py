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
        date_pretty_printer = lambda x: x.strftime("%B %d, %Y")
        admins = ((x.get_email(), x.get_name(), x.get_email() == case.assignee) for x in web.ctx.site.get("/usergroup/admin").members)
        case.update_message_count(user.get_email())
        return render_template("admin/case", case, admins, date_pretty_printer)

    def POST(self, caseid):
        if not support_db:
            return render_template("admin/cases", None, None, True, False)
        case = support_db.get_case(caseid)
        form = web.input()
        action = form.get("button","")
        {"SEND REPLY" : self.POST_sendreply,
         "UPDATE"     : self.POST_update,
         "CLOSE CASE" : self.POST_closecase,
         "OPEN CASE"  : self.POST_opencase,
         "REASSIGN"   : self.POST_reassign}[action](form,case)
        date_pretty_printer = lambda x: x.strftime("%B %d, %Y")
        admins = ((x.get_email(), x.get_name(), x.get_email() == case.assignee) for x in web.ctx.site.get("/usergroup/admin").members)
        return render_template("admin/case", case, admins, date_pretty_printer)
    
    def POST_reassign(self, form, case):
        user = web.ctx.site.get_user()
        assignee = form.get("assignee", False)
        if assignee != case.assignee:
            case.reassign(assignee, user.get_email(), '')
            subject = "Case #%s has been assigned to you"%case.caseno
            message = render_template("admin/email_reassign", case, '')
            web.sendmail(config.get("support_case_control_address","support@openlibrary.org"), assignee, subject, message)
            add_flash_message("info", "Case reassigned")

    def POST_sendreply(self, form, case):
        user = web.ctx.site.get_user()
        assignee = case.assignee
        casenote = form.get("casenote1", "")
        case.add_worklog_entry(by = user.get_email(),
                               text = casenote,
                               summary = "replied")
        case.change_status("replied", user.get_email())
        email_to = form.get("email", False)
        subject = "Case #%s: %s"%(case.caseno, case.subject)
        if assignee != user.get_email():
            case.reassign(user.get_email(), user.get_name(), "")
        if email_to:
            message = render_template("admin/email", case, casenote)
            web.sendmail(config.get("support_case_control_address","support@openlibrary.org"), email_to, subject, message)
        add_flash_message("info", "Reply sent")
        raise web.redirect("/admin/support")

    def POST_update(self, form, case):
        casenote = form.get("casenote2", False)
        user = web.ctx.site.get_user()
        by = user.get_email()
        text = casenote or ""
        if case.status == "closed":
            case.change_status("new", by)
        else:
            case.add_worklog_entry(by = by,
                                   text = text,
                                   summary = "commented")
        add_flash_message("info", "Case updated")


    def POST_opencase(self, form, case):
        user = web.ctx.site.get_user()
        by = user.get_email()
        case.add_worklog_entry(by = by,
                               text = '',
                               summary = "opened the case")
        case.change_status("new", by)
        add_flash_message("info", "Case reopened")

    def POST_closecase(self, form, case):
        user = web.ctx.site.get_user()
        by = user.get_email()
        case.add_worklog_entry(by = by,
                               text = '',
                               summary = "closed the case")
        case.change_status("closed", by)
        add_flash_message("info", "Case closed")


def setup():
    global support_db
    try:
        support_db = support.Support()
    except support.DatabaseConnectionError:
        support_db = None



