# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

bp = Blueprint('json_format', __name__, url_prefix='/json-format')


@bp.route('/')
def page():
    return render_template('json_format.html')
