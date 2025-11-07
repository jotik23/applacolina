from __future__ import annotations

from django.db import migrations, models


def _column_exists(schema_editor, table: str, column: str) -> bool:
    query = """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(query, [table, column])
        return cursor.fetchone() is not None


def _add_field_if_missing(field_name: str, field_factory):
    def inner(apps, schema_editor):
        model = apps.get_model('administration', 'PurchaseRequest')
        table = model._meta.db_table
        if _column_exists(schema_editor, table, field_name):
            return
        field = field_factory()
        field.set_attributes_from_name(field_name)
        schema_editor.add_field(model, field)

    return inner


def _field_factory(**kwargs):
    def builder():
        return kwargs['field'].clone()

    return builder


def _operation(name: str, field: models.Field) -> migrations.SeparateDatabaseAndState:
    return migrations.SeparateDatabaseAndState(
        database_operations=[
            migrations.RunPython(
                _add_field_if_missing(name, _field_factory(field=field)),
                migrations.RunPython.noop,
            )
        ],
        state_operations=[
            migrations.AddField(
                model_name='purchaserequest',
                name=name,
                field=field,
            )
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0014_supplier_bank_fields'),
    ]

    operations = [
        _operation(
            'delivery_condition',
            models.CharField(
                'Condiciones de entrega',
                max_length=20,
                choices=[('immediate', 'Entrega inmediata'), ('shipping', 'Envío posterior')],
                default='immediate',
            ),
        ),
        _operation(
            'payment_condition',
            models.CharField(
                'Condiciones de pago',
                max_length=20,
                choices=[('contado', 'Contado'), ('credito', 'Crédito')],
                blank=True,
            ),
        ),
        _operation(
            'payment_method',
            models.CharField(
                'Medio de pago',
                max_length=20,
                choices=[('efectivo', 'Efectivo'), ('transferencia', 'Transferencia')],
                blank=True,
            ),
        ),
        _operation(
            'payment_source',
            models.CharField(
                'Origen del pago',
                max_length=20,
                choices=[('tbd', 'Por definir (TBD)'), ('operations', 'Operaciones'), ('finance', 'Finanzas')],
                default='tbd',
            ),
        ),
        _operation(
            'purchase_date',
            models.DateField('Fecha de compra', blank=True, null=True),
        ),
        _operation(
            'delivery_terms',
            models.TextField('Condiciones de entrega (legacy)', blank=True, default=''),
        ),
        _operation(
            'shipping_eta',
            models.DateField('Fecha estimada de llegada', blank=True, null=True),
        ),
        _operation(
            'shipping_notes',
            models.TextField('Notas de envío', blank=True),
        ),
        _operation(
            'supplier_account_holder_id',
            models.CharField('Identificación titular (compra)', max_length=50, blank=True),
        ),
        _operation(
            'supplier_account_holder_name',
            models.CharField('Nombre titular (compra)', max_length=255, blank=True),
        ),
        _operation(
            'supplier_account_number',
            models.CharField('Número de cuenta (compra)', max_length=60, blank=True),
        ),
        _operation(
            'supplier_account_type',
            models.CharField(
                'Tipo de cuenta (compra)',
                max_length=20,
                choices=[('ahorros', 'Ahorros'), ('corriente', 'Corriente')],
                blank=True,
            ),
        ),
        _operation(
            'supplier_bank_name',
            models.CharField('Banco (compra)', max_length=120, blank=True),
        ),
    ]
