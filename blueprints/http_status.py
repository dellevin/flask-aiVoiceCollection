# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

bp = Blueprint('http_status', __name__, url_prefix='/http-status')


@bp.route('/')
def page():
    return render_template('http_status.html')
