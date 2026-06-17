# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

bp = Blueprint('token_gen', __name__, url_prefix='/token-gen')


@bp.route('/')
def page():
    return render_template('token_gen.html')
