# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

bp = Blueprint('qr_code', __name__, url_prefix='/qr-code')


@bp.route('/')
def page():
    return render_template('qr_code.html')
