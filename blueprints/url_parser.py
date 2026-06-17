# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

bp = Blueprint('url_parser', __name__, url_prefix='/url-parser')


@bp.route('/')
def page():
    return render_template('url_parser.html')
