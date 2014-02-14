from __future__ import unicode_literals

import string

from django.db import models
from django.utils.encoding import python_2_unicode_compatible

from moneyed import Money

from ..fields import MoneyField
from ..goods.models import Product
from ..settings import bazaar_settings


class ListingManager(models.Manager):
    def low_stock_ids(self):
        """
        Returns a list of ids of the listings for which the quantity of a product in stock
        is less than the amount needed to satisfy it
        """
        from django.db import connection
        cursor = connection.cursor()

        cursor.execute("""
            SELECT DISTINCT A.listing_id FROM
                (SELECT
                    "listings_publishing"."listing_id",
                    "listings_listingset"."product_id",
                    "listings_publishing"."available_units" * "listings_listingset"."quantity"
                      AS "needed"
                FROM "listings_publishing"
                    JOIN "listings_listingset"
                    ON "listings_publishing"."listing_id" = "listings_listingset"."listing_id") AS A
            JOIN
                (SELECT
                    "listings_listingset"."product_id",
                    "warehouse_stock"."quantity" -
                    SUM("listings_listingset"."quantity" * "listings_order"."quantity") as "available"
                FROM "listings_order"
                    JOIN "listings_publishing"
                    ON "listings_order"."publishing_id" = "listings_publishing"."id"
                    JOIN "listings_listingset"
                    ON "listings_publishing"."listing_id" = "listings_listingset"."listing_id"
                    JOIN "warehouse_stock"
                    ON "warehouse_stock"."product_id" = "listings_listingset"."product_id"

                WHERE "listings_order"."status" = %s
                GROUP BY "listings_listingset"."product_id", "warehouse_stock"."quantity") AS B
            ON A.product_id = B.product_id
            WHERE A.needed > B.available
            """, [Order.ORDER_PENDING])

        res = cursor.fetchall()
        return [r[0] for r in res]

    def low_cost_ids(self):
        """
        Returns a list of ids of the listings that have a publishing with a price lower
        than their cost
        """
        from django.db import connection
        cursor = connection.cursor()

        cursor.execute("""SELECT DISTINCT l.id
            FROM listings_listing AS l JOIN listings_publishing AS pu
            ON l.id = pu.listing_id
            WHERE pu.price < (SELECT SUM(s.price * ls.quantity)
                FROM listings_listing AS l1 JOIN listings_listingset AS ls
                ON l1.id = ls.listing_id JOIN goods_product AS p
                ON p.id = ls.product_id LEFT JOIN warehouse_stock AS s
                ON p.id = s.product_id
                WHERE l1.id = l.id)""")

        res = cursor.fetchall()
        return [r[0] for r in res]


@python_2_unicode_compatible
class Listing(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField(max_length=500, blank=True)

    picture_url = models.URLField(blank=True)
    products = models.ManyToManyField(Product, related_name="listings", through="ListingSet")

    objects = ListingManager()

    class Meta:
        ordering = ["title"]

    @property
    def available_units(self):
        """
        Returns available units across all publishings
        """
        available = 0
        for publishing in self.publishings.all():
            available += publishing.available_units

        return available

    @property
    def cost(self):
        """
        Returns global cost for the listing
        """
        cost = Money(0.00, bazaar_settings.DEFAULT_CURRENCY)
        for ls in self.listing_sets.all():
            try:
                avg_cost = ls.product.stock.price
            except models.ObjectDoesNotExist:
                avg_cost = Money(0, bazaar_settings.DEFAULT_CURRENCY)

            cost += avg_cost * ls.quantity

        return cost

    def is_stock_low(self):
        """
        Returns True when products stock cannot satisfy published listings
        """
        for ls in self.listing_sets.all():
            try:
                product_quantity = ls.product.stock.available
            except models.ObjectDoesNotExist:
                product_quantity = 0

            if product_quantity < self.available_units * ls.quantity:
                return True

    def is_low_cost(self):
        listing_cost = self.cost
        for publishing in self.publishings.all():
            if listing_cost > publishing.price:
                return True

    def __str__(self):
        return self.title


class ListingSet(models.Model):
    quantity = models.DecimalField(max_digits=30, decimal_places=4, default=1)

    product = models.ForeignKey(Product, related_name="listing_sets")
    listing = models.ForeignKey(Listing, related_name="listing_sets")


@python_2_unicode_compatible
class Store(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    url = models.URLField(blank=True)

    def __str__(self):
        return self.name


@python_2_unicode_compatible
class Publishing(models.Model):
    external_id = models.CharField(max_length=128, db_index=True)

    original_price = MoneyField()
    price = MoneyField()

    available_units = models.IntegerField(default=0)

    pub_date = models.DateTimeField(null=True, blank=True)
    last_modified = models.DateTimeField(auto_now=True)

    status = models.CharField(max_length=50, blank=True)

    listing = models.ForeignKey(Listing, related_name="publishings")
    store = models.ForeignKey(Store, related_name="publishings")

    def is_active(self):
        return self.status.lower() in map(string.lower, bazaar_settings.PUBLISHING_ACTIVE_STATUS)

    def __str__(self):
        return "Publishing %s on %s" % (self.external_id, self.store)


@python_2_unicode_compatible
class Order(models.Model):
    ORDER_PENDING = 0
    ORDER_COMPLETED = 1
    ORDER_STATUS_CHOICES = (
        (ORDER_PENDING, "Pending"),
        (ORDER_COMPLETED, "Completed"),
    )
    external_id = models.CharField(max_length=256)
    store = models.ForeignKey(Store)
    publishing = models.ForeignKey(Publishing, null=True, blank=True)

    processed = models.BooleanField(default=False)
    bypass = models.BooleanField(default=False)

    quantity = models.IntegerField(default=1)

    status = models.IntegerField(max_length=50, choices=ORDER_STATUS_CHOICES, default=ORDER_PENDING)

    def __str__(self):
        return "Order %s from %s" % (self.external_id, self.store)
