from odoo import fields, models


class WebsiteEventMenu(models.Model):
    _inherit = 'website.event.menu'

    menu_type = fields.Selection(
        selection_add=[
            ('conference_cfp', 'Call for Papers Menus'),
            ('conference_materials', 'Conference Materials Menus'),
        ],
        ondelete={'conference_cfp': 'cascade', 'conference_materials': 'cascade'})
