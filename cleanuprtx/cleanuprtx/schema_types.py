"""schema.org type families the rules reason about. Stdlib only.

ORG_TYPES is the transitive subclass closure of schema.org Organization
(LocalBusiness subtree included). Google accepts any subtype wherever it asks
for Organization. is_org_type() also applies a naming heuristic for subtypes
added to schema.org after this list was written, so a valid newer type is
never a false positive.
"""

from __future__ import annotations

import re

PERSON_TYPES = frozenset({"Person", "Patient"})

ORG_TYPES = frozenset({
    "Organization", "Airline", "Consortium", "Cooperative", "Corporation", "FundingScheme",
    "GovernmentOrganization", "LibrarySystem", "NGO", "NewsMediaOrganization", "OnlineBusiness",
    "OnlineStore", "PoliticalParty", "Project", "FundingAgency", "ResearchProject",
    "ResearchOrganization", "SearchRescueOrganization", "SportsOrganization", "SportsTeam",
    "WorkersUnion", "PerformingGroup", "DanceGroup", "MusicGroup", "TheaterGroup",
    "EducationalOrganization", "CollegeOrUniversity", "ElementarySchool", "HighSchool",
    "MiddleSchool", "Preschool", "School",
    "MedicalOrganization", "Dentist", "DiagnosticLab", "Hospital", "MedicalClinic",
    "CovidTestingFacility", "Pharmacy", "Physician", "IndividualPhysician", "PhysiciansOffice",
    "VeterinaryCare",
    # LocalBusiness subtree
    "LocalBusiness", "AnimalShelter", "ArchiveOrganization", "AutomotiveBusiness", "AutoBodyShop",
    "AutoDealer", "AutoPartsStore", "AutoRental", "AutoRepair", "AutoWash", "GasStation",
    "MotorcycleDealer", "MotorcycleRepair", "ChildCare", "DryCleaningOrLaundry", "EmergencyService",
    "FireStation", "PoliceStation", "EmploymentAgency", "EntertainmentBusiness",
    "AdultEntertainment", "AmusementPark", "ArtGallery", "Casino", "ComedyClub", "MovieTheater",
    "NightClub", "FinancialService", "AccountingService", "AutomatedTeller", "BankOrCreditUnion",
    "InsuranceAgency", "FoodEstablishment", "Bakery", "BarOrPub", "Brewery", "CafeOrCoffeeShop",
    "Distillery", "FastFoodRestaurant", "IceCreamShop", "Restaurant", "Winery", "GovernmentOffice",
    "PostOffice", "HealthAndBeautyBusiness", "BeautySalon", "DaySpa", "HairSalon", "HealthClub",
    "NailSalon", "TattooParlor", "HomeAndConstructionBusiness", "Electrician", "GeneralContractor",
    "HVACBusiness", "HousePainter", "Locksmith", "MovingCompany", "Plumber", "RoofingContractor",
    "InternetCafe", "LegalService", "Attorney", "Notary", "Library", "LodgingBusiness",
    "BedAndBreakfast", "Campground", "Hostel", "Hotel", "Motel", "Resort", "SkiResort",
    "VacationRental", "MedicalBusiness", "CommunityHealth", "Dermatology", "DietNutrition",
    "Emergency", "Geriatric", "Gynecologic", "MedicalClinic", "Midwifery", "Nursing",
    "Obstetric", "Oncologic", "Optician", "Optometric", "Otolaryngologic", "Pediatric",
    "Physiotherapy", "PlasticSurgery", "Podiatric", "PrimaryCare", "Psychiatric", "PublicHealth",
    "ProfessionalService", "RadioStation", "RealEstateAgent", "RecyclingCenter", "SelfStorage",
    "ShoppingCenter", "SportsActivityLocation", "BowlingAlley", "ExerciseGym", "GolfCourse",
    "PublicSwimmingPool", "SportsClub", "StadiumOrArena", "TennisComplex", "Store", "BikeStore",
    "BookStore", "ClothingStore", "ComputerStore", "ConvenienceStore", "DepartmentStore",
    "ElectronicsStore", "Florist", "FurnitureStore", "GardenStore", "GroceryStore",
    "HardwareStore", "HobbyShop", "HomeGoodsStore", "JewelryStore", "LiquorStore",
    "MensClothingStore", "MobilePhoneStore", "MovieRentalStore", "MusicStore",
    "OfficeEquipmentStore", "OutletStore", "PawnShop", "PetStore", "ShoeStore",
    "SportingGoodsStore", "TireShop", "ToyStore", "WholesaleStore", "TelevisionStation",
    "TouristInformationCenter", "TravelAgency",
})

# Only suffixes that schema.org uses exclusively for Organization subtypes.
# "Service", "Station", "Group", "Office", "Center", "Club", "Team" also name
# Services, Places and Products and are deliberately absent.
_ORG_SUFFIX = re.compile(
    r"(Organization|Organisation|Business|Store|Shop|Company|Contractor|Agency|Dealer|"
    r"Restaurant|School|University|College|Hospital|Hotel|Bank|Firm)$"
)


def is_person_type(name: str) -> bool:
    return name in PERSON_TYPES


def is_org_type(name: str) -> bool:
    return name in ORG_TYPES or bool(_ORG_SUFFIX.search(name or ""))


def is_entity_type(name: str) -> bool:
    """Person or any Organization subtype."""
    return is_person_type(name) or is_org_type(name)
